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
can't use ParallelAgent. The parallel phases (claim_extractor ×2,
notifier ×N) run concurrently via `asyncio.gather` over the hardened
`_guarded` calls (hardening.py buffers each attempt's events to make
retry correct), then yield each successful branch's buffered events.

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

import asyncio
import json
import uuid
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.genai import types
from typing_extensions import override

# Relative imports: the Agent Engine deploy bundles only this package directory
# (same constraint noted in drift_analyzer/agent.py:35), so sibling packages are
# not importable but intra-package modules are. `__init__.py` already uses
# `from .agent import root_agent`, so the package is always imported as a
# package — relative imports resolve in both `adk web` and the deployed runtime.
from .hardening import (  # noqa: E402
    GuardedResult,
    RetryPolicy,
    SubAgentError,
    run_sub_agent_guarded,
)
from .schemas import OUTPUT_SCHEMAS  # noqa: E402

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

    Walk events in reverse; for each event, concatenate ALL its text parts
    (Gemini sometimes splits one JSON response across multiple text parts in
    the final event), strip any markdown code fence, try json.loads.

    Why text-part-only — including for tool-using agents like citation_finder /
    drift_analyzer / memory_synthesizer: their final §3.x.2 business output is
    always a *subsequent* text part that follows the tool round-trip. The
    function_response parts on the way there are MCP-wrapped tool returns
    shaped {"content":[{"type":"text","text":...}], "isError":bool}, NOT the
    agent's answer. A first-cut implementation walked function_response first
    and confidently returned the MCP wrapper as if it were the agent's output,
    which was silently fine for N=0 runs (the wrapper still has zero
    affected_citations) but caused supervisor to skip notifier fan-out on the
    first real N>0 e2e (T1, 2026-05-26). Mirrors the same lesson the
    dispatcher learned in changelog 2026-05-25.
    """
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


class SupervisorAgent(BaseAgent):
    """Custom BaseAgent that orchestrates the §4.1 fan-out across remote
    reasoning engines on Vertex AI Agent Engine.

    Hardening (C2 / D5): every sub-agent call goes through `_guarded`, which
    adds a per-attempt timeout, exponential-backoff retry, and a deterministic
    §3.x.2 schema gate (hardening.py + schemas.py). This is pure code — the
    supervisor remains "NOT an LLM agent". Failure handling is per-phase
    (core chain fail-fast; notifier skips the one citation; memory_synthesizer
    logs and does not block), wired in `_run_async_impl` below.
    """

    # Single shared retry policy for all sub-agents. Tunable per-call if a
    # specific agent ever needs different bounds; today one policy fits all.
    _retry_policy: RetryPolicy = RetryPolicy()

    async def _guarded(
        self,
        agent_name: str,
        envelope: dict,
        user_id: str,
        *,
        require_output: bool = True,
    ) -> GuardedResult:
        """Run one sub-agent call with timeout + retry + schema validation.

        Returns a GuardedResult (buffered events of the successful attempt +
        validated §3.x.2 output). Raises SubAgentError on exhaustion; the
        caller decides the per-phase degradation policy.
        """
        return await run_sub_agent_guarded(
            agent_name=agent_name,
            call_factory=lambda: _call_sub_agent(agent_name, envelope, user_id),
            extractor=_extract_final_output,
            schema=OUTPUT_SCHEMAS.get(agent_name),
            policy=self._retry_policy,
            require_output=require_output,
        )

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

        # Guard each extractor independently (timeout + backoff retry + schema
        # gate). Both are on the core chain, so either failing is fail-fast:
        # drift_analyzer cannot run without both claim sets. Run them
        # concurrently (matching the original ×2 parallelism), then yield each
        # successful attempt's buffered events.
        extract_results = await asyncio.gather(
            self._guarded("claim_extractor", preprint_env, user_id),
            self._guarded("claim_extractor", published_env, user_id),
            return_exceptions=True,
        )
        for r in extract_results:
            if isinstance(r, GuardedResult):
                for ev in r.events:
                    yield ev
        # Surface the first failure deterministically (preprint before published)
        # after streaming whatever did succeed, so the dispatcher sees partial
        # progress before the error.
        for r in extract_results:
            if isinstance(r, BaseException):
                raise RuntimeError(
                    "claim_extractor failed on the core chain (preprint/published "
                    "claim sets are both required by drift_analyzer)"
                ) from r

        preprint_claims = extract_results[0].output
        published_claims = extract_results[1].output

        # --- Phase 2: drift_analyzer ------------------------------------
        drift_env = {
            "preprint_doi": preprint["doi"],
            "preprint_version_compared": preprint.get("version"),
            "published_doi": published["doi"],
            "preprint_claims": preprint_claims.get("claims", []),
            "published_claims": published_claims.get("claims", []),
        }
        # Core chain: drift_analyzer failure is fail-fast (citation_finder and
        # everything after need its output). The schema gate here is the one
        # that matters most — a truthy-but-wrong drift_event would poison the
        # minted event_id and every downstream ES row (T1 2026-05-26).
        try:
            drift_result = await self._guarded("drift_analyzer", drift_env, user_id)
        except SubAgentError as exc:
            raise RuntimeError(
                "drift_analyzer failed on the core chain; cannot continue"
            ) from exc
        for event in drift_result.events:
            yield event
        drift_event = drift_result.output

        # Mint event_id here (supervisor IS the §3.2.2 orchestrator). drift_analyzer
        # returns event_id=null by design — same orchestrator-fills rationale as
        # analyzed_at / drafted_at / synthesized_at. Without this mint, citation_finder
        # gets drift_event_id=None and notifier gets affected_citation_id=f"None::{doi}",
        # poisoning both ES rows and any future BFF join (T1 finding 2026-05-26).
        if not drift_event.get("event_id"):
            drift_event["event_id"] = str(uuid.uuid4())

        # Emit the minted drift_event back into the stream so the dispatcher can pick
        # up the SAME event_id we use for downstream fan-out envelopes. Without this,
        # dispatcher parses drift_analyzer's text part (event_id=null), mints its own
        # UUID for `drift_events._id`, and ends up with a different UUID than the one
        # baked into affected_citations.drift_event_id / notification_log.drift_event_id
        # — breaking any future BFF join (T1 round-3 finding 2026-05-26 Bug 4).
        # The event carries the COMPLETE post-mint drift_event JSON so dispatcher can
        # rely on it as the authoritative §3.2.2 output.
        #
        # invocation_id is a Pydantic-required field on Event (ADK 1.34.0). Omitting
        # it raised ValidationError on yield, which silently terminated the async
        # generator after drift_analyzer and stranded citation_finder / notifier /
        # memory_synthesizer (T1 round-4 finding 2026-05-26).
        yield Event(
            author="supervisor_agent",
            invocation_id=ctx.invocation_id,
            content=types.Content(
                role="model",
                parts=[types.Part(text=json.dumps(drift_event, ensure_ascii=False))],
            ),
        )

        # --- Phase 3: citation_finder -----------------------------------
        citation_env = {
            "drift_event_id": drift_event.get("event_id"),
            "preprint_doi": preprint["doi"],
            "drift_summary": drift_event.get("drift_summary"),
            "claim_diffs": drift_event.get("claim_diffs", []),
        }
        # Core chain: citation_finder failure is fail-fast (notifier fan-out and
        # memory_synthesizer's affected_citations_summary both depend on it).
        try:
            citation_guarded = await self._guarded("citation_finder", citation_env, user_id)
        except SubAgentError as exc:
            raise RuntimeError(
                "citation_finder failed on the core chain; cannot continue"
            ) from exc
        for event in citation_guarded.events:
            yield event
        citation_result = citation_guarded.output

        # --- Phase 4: notifier ×N in DYNAMIC PARALLEL --------------------
        # ParallelAgent.sub_agents is fixed at construction; for per-element
        # fan-out we build the envelope list dynamically and run the guarded
        # notifier calls concurrently.
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

            # STREAM the fan-out: previously this awaited asyncio.gather (which
            # blocks until ALL notifiers finish) and only then yielded every
            # buffered event at once — so the UI saw a long silent gap after
            # citation_finder, then ~13 notifier lanes light up simultaneously.
            # Instead, run all notifiers concurrently but yield each one's events
            # AS IT COMPLETES (asyncio.as_completed), so the board fills in 1/N,
            # 2/N, … live. Concurrency is identical to gather (all tasks start at
            # once); only the collection timing changes.
            #
            # Per-citation degradation is preserved: each notifier runs inside
            # its own coroutine that catches SubAgentError (and any unexpected
            # error) and reports it as a per-citation skip event, so one
            # recipient failing never aborts the others or the completed
            # analysis — the same guarantee the old return_exceptions=True gave.
            async def _run_notifier(env: dict) -> tuple[dict, GuardedResult | BaseException]:
                try:
                    return env, await self._guarded("notifier", env, user_id)
                except BaseException as exc:  # noqa: BLE001 — mirror gather(return_exceptions=True)
                    return env, exc

            tasks = [
                asyncio.ensure_future(_run_notifier(env)) for env in notifier_envelopes
            ]
            for fut in asyncio.as_completed(tasks):
                env, r = await fut
                if isinstance(r, GuardedResult):
                    for ev in r.events:
                        yield ev
                else:
                    # Skipped: surface a single agent.failed-style event so the
                    # dispatcher/frontend show this one email was dropped, rather
                    # than silently vanishing. Other notifiers are unaffected.
                    yield Event(
                        author="notifier",
                        invocation_id=ctx.invocation_id,
                        error_code="sub_agent_failed",
                        error_message=(
                            f"notifier skipped for "
                            f"{env.get('affected_citation_id')}: {r}"
                        ),
                    )

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
        # Terminal async step: memory_synthesizer failing must NOT fail the run.
        # The drift_event + citations + emails are already done and persisted;
        # a missed memory write only means this one event didn't update the
        # pattern store (the curator / a later event can still pick it up). Log
        # via an error event and finish cleanly.
        try:
            memory_guarded = await self._guarded("memory_synthesizer", memory_env, user_id)
        except SubAgentError as exc:
            yield Event(
                author="memory_synthesizer",
                invocation_id=ctx.invocation_id,
                error_code="sub_agent_failed",
                error_message=f"memory_synthesizer skipped (non-blocking): {exc}",
            )
        else:
            for event in memory_guarded.events:
                yield event


root_agent = SupervisorAgent(
    name="supervisor_agent",
    description=(
        "Orchestrates the §4.1 main flow across 5 sub-agents on Agent Engine. "
        "Pure ADK orchestration — no LLM logic of its own."
    ),
)
