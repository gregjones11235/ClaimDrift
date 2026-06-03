"""Minimal JSON-Schema gates for the sub-agents' §3.x.2 outputs (C2 / D5).

These are DELIBERATELY minimal — they assert the *structural invariants the
supervisor's own downstream fan-out depends on*, not the full §3.x.2 surface.
The goal is to catch the class of bug that has actually bitten us: a sub-agent
returning a truthy-but-wrong object (an MCP wrapper, a partial JSON, a
hallucinated shape) that `_extract_final_output` happily parses and the
supervisor then feeds into the next phase, poisoning ES rows downstream
(T1 2026-05-26 findings; contracts.md §3.2.2 event_id, §3.3.2, §3.4.2).

Why minimal and not exhaustive:
- The supervisor is "NOT an LLM agent — pure ADK orchestration"
  (agent.py:3). Its job is to route, not to grade content quality. Over-strict
  schemas would reject legitimately-varied LLM output and make the pipeline
  brittle for no orchestration benefit.
- The full §3.x.2 contract is enforced where the data is *written* (the
  dispatcher / ES mappings), not at the orchestration seam. Here we only need
  "is this the right *kind* of object to pass to the next phase?".

Each schema is Draft 2020-12. `additionalProperties` is left open on purpose —
sub-agents may carry extra reasoning fields; we assert presence + type of the
keys the supervisor reads, nothing more.
"""
from __future__ import annotations

from typing import Any

# claim_extractor §3.1.2 — supervisor reads `.claims` (a list) at agent.py:247.
CLAIM_EXTRACTOR_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["claims"],
    "properties": {
        "claims": {"type": "array"},
    },
}

# drift_analyzer §3.2.2 — supervisor reads drift_summary / claim_diffs and mints
# event_id (agent.py:264, 291-294). event_id may be null on input (orchestrator
# fills it), so it is NOT required here; we require the fields the fan-out reads.
DRIFT_ANALYZER_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["drift_summary", "claim_diffs"],
    "properties": {
        "drift_summary": {"type": "string"},
        "claim_diffs": {"type": "array"},
        "materiality_score": {"type": ["number", "null"]},
        # event_id is minted by the supervisor when null/absent; accept both.
        "event_id": {"type": ["string", "null"]},
    },
}

# citation_finder §3.3.2 — supervisor reads `.affected_citations` (a list) to
# build the notifier fan-out (agent.py:309). An empty list is valid (N=0 run).
CITATION_FINDER_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["affected_citations"],
    "properties": {
        "affected_citations": {"type": "array"},
    },
}

# notifier §3.4.2 — supervisor does not read notifier output to drive any
# further phase; the dispatcher persists it. We still gate it so a per-citation
# failure is detected and that one email can be skipped (not silently counted as
# sent). Require the id that ties it back to notification_log._id.
NOTIFIER_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["affected_citation_id"],
    "properties": {
        "affected_citation_id": {"type": "string"},
        "subject": {"type": ["string", "null"]},
        "body": {"type": ["string", "null"]},
    },
}

# memory_synthesizer §3.5.2 — terminal async step; supervisor reads nothing from
# it. Gate only that it's the right kind of object so a garbage return is
# logged rather than passed on. `action` is the create-vs-update discriminator.
MEMORY_SYNTHESIZER_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {"enum": ["create_new", "update_existing"]},
    },
}

# Map agent_name -> its output schema. Used by hardening.run_sub_agent_guarded.
OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "claim_extractor": CLAIM_EXTRACTOR_OUTPUT,
    "drift_analyzer": DRIFT_ANALYZER_OUTPUT,
    "citation_finder": CITATION_FINDER_OUTPUT,
    "notifier": NOTIFIER_OUTPUT,
    "memory_synthesizer": MEMORY_SYNTHESIZER_OUTPUT,
}
