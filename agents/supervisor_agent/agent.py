"""
Supervisor — orchestrates the §4.1 main flow across the 5 already-deployed
sub-agents on Vertex AI Agent Engine. NOT an LLM agent — pure ADK
orchestration code per §9.6.1.

Owns:    contracts.md §4.1 fan-out (claim_extractor ×2 parallel
         → drift_analyzer → citation_finder → notifier ×N parallel
         → memory_synthesizer per §9.6.1 5g)
Reads:   nothing directly (sub-agents read ES via Elastic MCP)
Writes:  nothing directly (Cloud Run dispatcher persists side effects)

# Why this exists

§9.6.1 inverted-topology decision: Elastic Workflow can't natively invoke a
Vertex Agent Engine reasoning engine, so the supervisor on Agent Engine drives
the fan-out. The dispatcher Cloud Run service routes the Workflow trigger
into a supervisor stream_query call, then consumes the merged event stream
to persist results and send emails.

# Why custom BaseAgent and not SequentialAgent / ParallelAgent

ADK has no built-in class for wrapping a remote reasoning engine as a
sub-agent (verified against google-adk 1.34.0; see contracts.md §9.6.1
2026-05-24 entry). The canonical alternative — endorsed by
https://adk.dev/agents/custom-agents/ — is a BaseAgent subclass whose
_run_async_impl(ctx) yields Event objects manually. We use that pattern.

Also: ParallelAgent.sub_agents is fixed at construction time, so the
per-citation notifier fan-out (N unknown until citation_finder returns)
can't use ParallelAgent. We borrow `_merge_agent_run` from
google.adk.agents.parallel_agent for the dynamic case.

# Event handling

Transparent pass-through (option P in design chat 2026-05-24):
every event from every sub-agent's stream is yielded as-is. The dispatcher
identifies sub-agent outputs by ADK's `function_response.name` field; final
schema-shaped outputs (§3.2.2 / §3.3.2 / §3.4.2) live in `response`. Demo
value: Vertex Playground shows the full inner activity of each sub-agent.

# Input contract

`ctx.user_content.parts[0].text` is a JSON string with shape:
  { "preprint":  { "doi", "version", "title", "abstract", "conclusion" },
    "published": { "doi", "version", "title", "abstract", "conclusion" } }
Dispatcher serializes this; we json.loads here. Sub-agents receive the
§3.x.1 envelope as a JSON-stringified message — same convention.
"""

from __future__ import annotations

import json
import sys
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.parallel_agent import (
    _merge_agent_run,
    _merge_agent_run_pre_3_11,
)
from google.adk.events.event import Event
from google.genai import types
from typing_extensions import override

# Sub-agent reasoning engine IDs from agents/_DEPLOY_CHECKLIST.md (Phase 5a-5e).
# Hard-coded because they are stable Vertex resource names, not secrets, and
# they pin supervisor to specific deployed sub-agent versions (re-deploys in
# place via --agent_engine_id keep the same id).
SUB_AGENT_IDS = {
    "claim_extractor": "2286406392413683712",   # Phase 5c
    "drift_analyzer":  "5333654490283245568",   # Phase 5a
    "citation_finder": "6997171602643222528",   # Phase 5e
    "notifier":        "7063036747193516032",   # Phase 5b
    # memory_synthesizer (Phase 4b) — supervisor's final async step per 5g.
    "memory_synthesizer": "8580327609152307200",
}


def _get_remote_engine(agent_name: str) -> Any:
    """Lazy import so the module loads without vertexai credentials configured
    (useful for unit-test scaffolds). The deployed Agent Engine runtime always
    has ADC."""
    from vertexai import agent_engines
    return agent_engines.get(SUB_AGENT_IDS[agent_name])


async def _call_sub_agent(
    agent_name: str,
    envelope: dict,
    user_id: str,
) -> AsyncGenerator[Event, None]:
    """Stream a single sub-agent invocation. Yields each chunk re-wrapped as
    an ADK Event with author=<agent_name>.

    Remote returns dicts (parsed JSON) shaped like Event.model_dump(); we
    re-hydrate into Event so downstream consumers can treat the stream
    uniformly.
    """
    engine = _get_remote_engine(agent_name)
    message = json.dumps(envelope, ensure_ascii=False)
    async for chunk in engine.async_stream_query(message=message, user_id=user_id):
        try:
            event = Event.model_validate(chunk)
            if not event.author:
                event.author = agent_name
        except Exception:
            # Fallback: wrap unstructured dict as a text event so dispatcher
            # can still see it.
            event = Event(
                author=agent_name,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=json.dumps(chunk, ensure_ascii=False))],
                ),
            )
        yield event


def _strip_markdown_fence(text: str) -> str:
    """Remove a leading ```json / ``` fence and trailing ``` if present.
    Gemini often wraps JSON output in a markdown code fence regardless of
    INSTRUCTION saying "JSON only".
    """
    s = text.strip()
    if s.startswith("```"):
        # drop the opening fence line (```json\n or ```\n)
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        # drop trailing ```
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _extract_final_output(events: list[Event]) -> dict | None:
    """Pull the structured §3.x.2 output from a sub-agent's event list.

    Strategy (in order):
    1. Scan in reverse for a function_response part — the standard ADK shape
       when an agent surfaces a tool result as its final answer.
    2. For LlmAgents with no tools (claim_extractor, notifier), the final
       answer lives in text parts. Walk events in reverse; for each event,
       concatenate ALL its text parts (Gemini sometimes splits one JSON
       response across multiple text parts in the final event), strip any
       markdown code fence, and try json.loads.

    Returns None if neither shape produced parseable JSON.
    """
    for event in reversed(events):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "function_response", None):
                    return dict(part.function_response.response or {})

    for event in reversed(events):
        if not (event.content and event.content.parts):
            continue
        combined = "".join(
            part.text for part in event.content.parts if getattr(part, "text", None)
        )
        if not combined.strip():
            continue
        stripped = _strip_markdown_fence(combined)
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _merge_func():
    """Pick the correct merge helper based on Python version (mirrors
    ParallelAgent's own selection logic)."""
    return _merge_agent_run if sys.version_info >= (3, 11) else _merge_agent_run_pre_3_11


class SupervisorAgent(BaseAgent):
    """Custom BaseAgent that orchestrates the §4.1 fan-out across remote
    reasoning engines on Vertex AI Agent Engine."""

    @override
    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # --- Parse input envelope ---------------------------------------
        if not (ctx.user_content and ctx.user_content.parts):
            raise ValueError("supervisor requires user_content with at least one text part")
        message_text = ctx.user_content.parts[0].text or ""
        try:
            envelope = json.loads(message_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"supervisor message must be a JSON string with shape "
                f"{{preprint: {{...}}, published: {{...}}}}; got: {message_text[:200]}"
            ) from e

        preprint = envelope["preprint"]
        published = envelope["published"]
        user_id = ctx.user_id or f"supervisor::{preprint.get('doi', 'unknown')}"

        # --- Phase 1: claim_extractor ×2 in PARALLEL --------------------
        preprint_env = {
            "preprint_doi": preprint["doi"],
            "version": preprint.get("version"),
            "title": preprint.get("title"),
            "abstract": preprint.get("abstract"),
            "conclusion": preprint.get("conclusion"),
        }
        published_env = {
            "preprint_doi": published["doi"],  # §3.1.1 keys both by preprint_doi
            "version": published.get("version"),
            "title": published.get("title"),
            "abstract": published.get("abstract"),
            "conclusion": published.get("conclusion"),
        }

        preprint_events: list[Event] = []
        published_events: list[Event] = []

        async def _run_extract(env: dict, sink: list[Event]) -> AsyncGenerator[Event, None]:
            async for ev in _call_sub_agent("claim_extractor", env, user_id):
                sink.append(ev)
                yield ev

        async for event in _merge_func()(
            [
                _run_extract(preprint_env, preprint_events),
                _run_extract(published_env, published_events),
            ]
        ):
            yield event

        preprint_claims = _extract_final_output(preprint_events)
        published_claims = _extract_final_output(published_events)
        if not preprint_claims or not published_claims:
            raise RuntimeError(
                "claim_extractor did not return §3.1.2 structured output for "
                f"one or both versions (preprint ok={bool(preprint_claims)}, "
                f"published ok={bool(published_claims)})"
            )

        # --- Phase 2: drift_analyzer ------------------------------------
        drift_env = {
            "preprint_doi": preprint["doi"],
            "preprint_version_compared": preprint.get("version"),
            "published_doi": published["doi"],
            "preprint_claims": preprint_claims.get("claims", []),
            "published_claims": published_claims.get("claims", []),
        }
        drift_events: list[Event] = []
        async for event in _call_sub_agent("drift_analyzer", drift_env, user_id):
            drift_events.append(event)
            yield event

        drift_event = _extract_final_output(drift_events)
        if not drift_event:
            raise RuntimeError("drift_analyzer did not return §3.2.2 structured output")

        # --- Phase 3: citation_finder -----------------------------------
        citation_env = {
            "drift_event_id": drift_event.get("event_id"),
            "preprint_doi": preprint["doi"],
            "drift_summary": drift_event.get("drift_summary"),
            "claim_diffs": drift_event.get("claim_diffs", []),
        }
        citation_events: list[Event] = []
        async for event in _call_sub_agent("citation_finder", citation_env, user_id):
            citation_events.append(event)
            yield event

        citation_result = _extract_final_output(citation_events)
        if not citation_result:
            raise RuntimeError("citation_finder did not return §3.3.2 structured output")

        # --- Phase 4: notifier ×N in DYNAMIC PARALLEL --------------------
        # ParallelAgent.sub_agents is fixed at construction; for per-element
        # fan-out we build the generator list dynamically and feed
        # _merge_agent_run directly.
        affected_citations = citation_result.get("affected_citations", [])
        if affected_citations:
            notifier_envelopes = [
                {
                    "affected_citation_id": (
                        f"{drift_event.get('event_id')}::{c.get('citing_paper_doi')}"
                    ),
                    "drift_event_summary": drift_event.get("drift_summary"),
                    "claim_diffs": drift_event.get("claim_diffs", []),
                    "citing_paper_doi": c.get("citing_paper_doi"),
                    "citing_paper_title": c.get("citing_paper_title"),
                    # v0: fan out by first author. Richer recipient selection
                    # (e.g. fan out per author) is out of scope per §3.4.1.
                    "recipient": (c.get("citing_paper_authors") or [{}])[0],
                    "severity_tier": c.get("severity_tier"),
                    "severity_reasoning": c.get("severity_reasoning"),
                }
                for c in affected_citations
            ]
            notifier_runs = [
                _call_sub_agent("notifier", env, user_id) for env in notifier_envelopes
            ]
            async for event in _merge_func()(notifier_runs):
                yield event

        # --- Phase 5g: memory_synthesizer -------------------------------
        # §9.6.1 5g: cheapest implementation puts memory_synthesizer at the
        # end of supervisor's own fan-out. We await it here in v0 because
        # the supervisor stream is the only synchronization point — making
        # it truly async would require a separate Workflow watching
        # drift_events and is deferred.
        memory_env = {
            "trigger": "new_drift_event",
            "drift_event_id": drift_event.get("event_id"),
            "drift_event": drift_event,
            "affected_citations_summary": {
                "total_affected": citation_result.get("total_found", len(affected_citations)),
                "central_count": sum(1 for c in affected_citations if c.get("severity_tier") == "central"),
                "comparative_count": sum(1 for c in affected_citations if c.get("severity_tier") == "comparative"),
                "peripheral_count": sum(1 for c in affected_citations if c.get("severity_tier") == "peripheral"),
            },
        }
        async for event in _call_sub_agent("memory_synthesizer", memory_env, user_id):
            yield event


root_agent = SupervisorAgent(
    name="supervisor_agent",
    description=(
        "Orchestrates the §4.1 main flow across 5 sub-agents on Agent Engine. "
        "Pure ADK orchestration — no LLM logic of its own."
    ),
)
