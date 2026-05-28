"""ADK Event -> contracts.md §6.1 SSE envelope translator.

Pure functions; no I/O. Shared between:
  - dispatcher (writes translated envelopes to the `agent_events` index)
  - bff       (replays envelopes from `agent_events` to the frontend over SSE,
              and translates the golden JSONL stream in SSE_REPLAY_GOLDEN mode)

The translator is stateful only across one supervisor stream: a `TranslatorState`
tracks which sub-agents have already emitted `agent.started` so we synthesize
exactly one `agent.started` per (agent_id, invocation_id) pair. notifier fans
out N times, each with a distinct invocation_id, so each fan-out gets its own
started/completed bookends.

§6.1 envelope shape:
    {
      "event_type": "agent.started" | "agent.tool_call" | "agent.pattern_retrieved"
                    | "agent.completed" | "agent.failed" | "heartbeat",
      "agent_id":   "claim_extractor" | "drift_analyzer" | "citation_finder"
                    | "notifier" | "memory_synthesizer" | null,
      "drift_event_id": "<uuid>" | null,
      "timestamp":  "2026-05-20T...Z",
      "payload":    {...}
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


KNOWN_AGENT_IDS = {
    "claim_extractor",
    "drift_analyzer",
    "citation_finder",
    "notifier",
    "memory_synthesizer",
}

MEMORY_TOOL_NAMES = {"search_drift_patterns", "update_drift_pattern"}


@dataclass
class TranslatorState:
    """Per-stream state. Tracks which (agent, invocation) pairs have started.

    Reusing one state instance across a full supervisor stream produces exactly
    one `agent.started` per sub-agent invocation, matching the §6.1 lifecycle.
    """
    started_invocations: set[tuple[str, str]] = field(default_factory=set)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _adk_event_timestamp(event: dict[str, Any]) -> str:
    """ADK events carry Unix-epoch `timestamp` (float). Convert to ISO-8601 Z.

    Fall back to `now` if missing — never let a malformed timestamp break the
    stream, but log via the returned envelope's `timestamp` field invariant.
    """
    ts = event.get("timestamp")
    if isinstance(ts, (int, float)):
        return (
            datetime.fromtimestamp(ts, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return _now_iso()


def _strip_markdown_fence(text: str) -> str:
    """Strip ```json ... ``` fences. Copy of dispatcher helper to avoid cycle."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _try_parse_json(text: str) -> Any | None:
    try:
        return json.loads(_strip_markdown_fence(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _unwrap_mcp_response(fn_response: dict[str, Any]) -> Any | None:
    """Unwrap the MCP {"content":[{"type":"text","text":"<json>"}], "isError":..}
    envelope and parse the inner JSON. Returns the parsed inner dict, or None
    on any structural mismatch.
    """
    resp = fn_response.get("response")
    if not isinstance(resp, dict):
        return None
    content = resp.get("content")
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, dict):
        return None
    text = first.get("text")
    if not isinstance(text, str):
        return None
    return _try_parse_json(text)


def _extract_pattern_ids_from_search(parsed_response: Any) -> tuple[list[str], list[float]]:
    """Pull pattern_id column (+ scores if available) out of an
    `search_drift_patterns` ES|QL response. Returns ([], []) if unparseable.

    The MCP wrapper returns `{"results": [{type:"query",...}, {type:"esql_results",
    data:{columns:[...], values:[[row0col0, row0col1, ...], ...]}}, ...]`. We
    locate the `esql_results` block, find the `pattern_id` and `_score` column
    indices, and slice them out.
    """
    if not isinstance(parsed_response, dict):
        return [], []
    results = parsed_response.get("results")
    if not isinstance(results, list):
        return [], []
    for block in results:
        if not isinstance(block, dict) or block.get("type") != "esql_results":
            continue
        data = block.get("data") or {}
        columns = data.get("columns") or []
        values = data.get("values") or []
        if not columns or not values:
            return [], []
        try:
            id_idx = next(i for i, c in enumerate(columns) if c.get("name") == "pattern_id")
        except StopIteration:
            return [], []
        try:
            score_idx = next(i for i, c in enumerate(columns) if c.get("name") == "_score")
        except StopIteration:
            score_idx = None
        ids: list[str] = []
        scores: list[float] = []
        for row in values:
            if not isinstance(row, list) or id_idx >= len(row):
                continue
            pid = row[id_idx]
            if pid is None:
                continue
            ids.append(str(pid))
            if score_idx is not None and score_idx < len(row):
                v = row[score_idx]
                if isinstance(v, (int, float)):
                    scores.append(float(v))
        return ids, scores
    return [], []


def _input_summary(agent_id: str) -> str:
    """One-line, frontend-facing description per §6.1 `agent.started.input_summary`.

    We deliberately keep these static — the agent's *envelope* is more detail
    than the frontend needs ("Extracting claims from preprint v2 abstract +
    conclusion (4391 chars)" would just be noise).
    """
    return {
        "claim_extractor": "Extracting claims from preprint and published versions.",
        "drift_analyzer":  "Comparing claim sets and retrieving memory patterns.",
        "citation_finder": "Finding citing papers via OpenAlex.",
        "notifier":        "Drafting notification email for affected citation.",
        "memory_synthesizer": "Distilling drift event into reusable pattern.",
    }.get(agent_id, f"Running {agent_id}.")


def _output_summary(agent_id: str, parsed: dict | None) -> tuple[str, str | None]:
    """Build (output_summary, output_id) for §6.1 `agent.completed`. parsed is
    the JSON the agent wrote to its final text part; None means we couldn't parse
    it and we degrade gracefully.
    """
    if parsed is None:
        return (f"{agent_id} completed (output unparseable).", None)
    if agent_id == "claim_extractor":
        n = len(parsed.get("claims") or [])
        return (f"{n} claims extracted.", parsed.get("preprint_doi"))
    if agent_id == "drift_analyzer":
        n = len(parsed.get("claim_diffs") or [])
        eid = parsed.get("event_id")  # may be null pre-mint
        return (f"Drift analyzed: {n} claim diffs.", eid)
    if agent_id == "citation_finder":
        n = parsed.get("total_found")
        if n is None:
            n = len(parsed.get("affected_citations") or [])
        return (f"{n} affected citations scored.", parsed.get("drift_event_id"))
    if agent_id == "notifier":
        ac_id = parsed.get("affected_citation_id")
        return ("Notification drafted.", ac_id)
    if agent_id == "memory_synthesizer":
        action = parsed.get("action", "synthesized")
        pid = (parsed.get("pattern") or {}).get("pattern_id")
        return (f"Memory pattern {action}.", pid)
    return (f"{agent_id} completed.", None)


def _envelope(
    event_type: str,
    agent_id: str | None,
    drift_event_id: str | None,
    timestamp: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "agent_id": agent_id,
        "drift_event_id": drift_event_id,
        "timestamp": timestamp,
        "payload": payload,
    }


def translate_adk_event(
    event: dict[str, Any],
    state: TranslatorState,
    drift_event_id: str | None,
) -> list[dict[str, Any]]:
    """Translate one ADK Event into zero or more §6.1 envelopes.

    Emits:
      - `agent.started` on first sight of (author, invocation_id) — but ONLY for
        sub-agents in KNOWN_AGENT_IDS. supervisor_agent's own events
        (orchestration glue) are not surfaced to the frontend.
      - `agent.tool_call` for every `function_call` part.
      - `agent.pattern_retrieved` for `function_response` parts whose name is
        in MEMORY_TOOL_NAMES; payload is `{pattern_ids, scores}` (scores is
        informational per §6.1).
      - `agent.completed` for `text` parts that parse as JSON (= the agent's
        final §3.x.2 output).
      - `agent.failed` if `error_code` / `error_message` set on the event.

    `text` parts that do not parse as JSON are dropped (a non-final reasoning
    snippet would otherwise produce spurious `completed` events).
    """
    author = event.get("author") or ""
    if author not in KNOWN_AGENT_IDS:
        return []

    invocation_id = event.get("invocation_id") or ""
    ts = _adk_event_timestamp(event)
    out: list[dict[str, Any]] = []

    key = (author, invocation_id)
    if key not in state.started_invocations:
        state.started_invocations.add(key)
        out.append(_envelope(
            "agent.started", author, drift_event_id, ts,
            {"input_summary": _input_summary(author)},
        ))

    if event.get("error_code") or event.get("error_message"):
        out.append(_envelope(
            "agent.failed", author, drift_event_id, ts,
            {
                "error_message": event.get("error_message") or event.get("error_code") or "unknown error",
                "retry_count": 0,
            },
        ))
        return out

    parts = ((event.get("content") or {}).get("parts")) or []
    for part in parts:
        if "function_call" in part:
            fc = part["function_call"] or {}
            tool_name = fc.get("name") or "unknown_tool"
            args = fc.get("args") or {}
            out.append(_envelope(
                "agent.tool_call", author, drift_event_id, ts,
                {"tool_name": tool_name, "args": args},
            ))
        elif "function_response" in part:
            fr = part["function_response"] or {}
            tool_name = fr.get("name") or ""
            if tool_name not in MEMORY_TOOL_NAMES:
                continue
            if tool_name == "search_drift_patterns":
                parsed = _unwrap_mcp_response(fr)
                ids, scores = _extract_pattern_ids_from_search(parsed)
                if not ids:
                    continue
                out.append(_envelope(
                    "agent.pattern_retrieved", author, drift_event_id, ts,
                    {"pattern_ids": ids, "scores": scores},
                ))
            elif tool_name == "update_drift_pattern":
                # The pattern_id we wrote came from the agent's fn_call args,
                # not the response. We need to look back at the most recent
                # update_drift_pattern fn_call by this author — but the caller
                # already emitted that as an `agent.tool_call`. The frontend
                # highlights the union of pattern_retrieved + the update tool_call,
                # so we deliberately do NOT re-emit pattern_retrieved here.
                continue
        elif "text" in part:
            text = part.get("text") or ""
            parsed = _try_parse_json(text)
            if parsed is None or not isinstance(parsed, dict):
                continue
            summary, output_id = _output_summary(author, parsed)
            out.append(_envelope(
                "agent.completed", author, drift_event_id, ts,
                {"output_summary": summary, "output_id": output_id},
            ))

    return out


def translate_stream(
    adk_events: Iterable[dict[str, Any]],
    *,
    drift_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Translate an entire stream in one shot. Convenience for tests / replay."""
    state = TranslatorState()
    envelopes: list[dict[str, Any]] = []
    for ev in adk_events:
        envelopes.extend(translate_adk_event(ev, state, drift_event_id))
    return envelopes


def heartbeat(drift_event_id: str | None = None) -> dict[str, Any]:
    """Frontend uses this to detect stream liveness."""
    return _envelope("heartbeat", None, drift_event_id, _now_iso(), {})
