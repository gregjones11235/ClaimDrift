"""
Memory Synthesizer — distills drift events into reusable patterns.
                     The WRITE side of the memory loop. Project's #1 selling point.

Owns:    contracts.md §3.5 Memory Synthesizer input/output
Reads:   `drift_events` (input), `drift_patterns` (via search_drift_patterns —
         to decide create vs update)
Writes:  `drift_patterns` index — read back by Drift Analyzer next time

Tools:
- `search_drift_patterns` — same hybrid (BM25 + ELSER) retrieval Drift Analyzer
  uses. Memory Synthesizer queries with the new drift_event's `drift_summary`,
  per contracts.md §3.5.1.
- `create_drift_pattern` — write a brand-new pattern when no retrieved candidate
  is similar enough.
- `update_drift_pattern` — append the new event_id to an existing pattern's
  `source_event_ids` when retrieval surfaced a true match. v0 is
  append-evidence-only; description/tag refinement is deferred (see §3.5.1
  TODO).

Why two write tools instead of one upsert: the create-vs-update choice is the
core business decision of this agent. Putting it in the tool name (rather than
hiding it in a nullable pattern_id parameter) keeps the decision visible in the
trace and harder for the LLM to skip past. See conversation 2026-05-22.

Why no min_score on retrieval: same as Drift Analyzer. The underlying
retriever returns RRF scores which are rank-based and do not measure semantic
relevance in a way that supports a fixed threshold (the original §3.5.1
v0 design used `>= 0.75`; that rule was retired 2026-05-22). The LLM judges
each candidate by reading its description — see INSTRUCTION.

Fires ASYNC after Drift Analyzer writes a new drift_event. Non-blocking.

Phase 4b: the three tools are no longer Python FunctionTools. They live inside
Elastic Agent Builder (one ES|QL tool + two Workflow YAML tools, see contracts.md
§9.3) and are exposed to this agent via the Elastic MCP server at
`${KIBANA_URL}/api/agent_builder/mcp`. The MCP server also exposes ~16 unrelated
`platform.*` built-in tools (search, ES|QL, Streams, Cases, ...). We use
`tool_filter` to expose ONLY our three tools — Memory Synthesizer must not be
able to reach for `platform.core.execute_esql` or `platform.streams.*` writes,
both because that's out of its §3.5 scope and because it would pollute the
tool-call trace shown in the demo.

The Phase-3 Python implementations in agents/_shared/elastic_retrieval.py and
agents/_shared/elastic_write.py are NOT runtime any more — they stay in-repo
as behavioral reference spec / regression harness per §9.5.
"""

import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

# Inlined from agents/_shared/config.py:MODEL_PRO. The Agent Engine deploy
# packages only this subdirectory, so the `_shared` sibling package is not
# importable in the deployed runtime. Keep this constant in sync with
# _shared/config.py if it ever changes (it hasn't since v0).
MODEL_PRO = "gemini-2.5-pro"

_KIBANA_URL = os.environ["KIBANA_URL"].rstrip("/")
_ELASTIC_API_KEY = os.environ["ELASTIC_API_KEY"]

elastic_mcp_tools = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=f"{_KIBANA_URL}/api/agent_builder/mcp",
        headers={
            "Authorization": f"ApiKey {_ELASTIC_API_KEY}",
            "Accept": "application/json, text/event-stream",
            "kbn-xsrf": "claimdrift",
        },
    ),
    tool_filter=[
        "search_drift_patterns",
        "create_drift_pattern",
        "update_drift_pattern",
    ],
)

INSTRUCTION = """\
You distill drift events into reusable patterns. This is the write side of the
project's memory loop — what you produce will be retrieved by future Drift
Analyzer runs to inform their reasoning. The single most important quality
of your output is whether the `pattern_description` text you write/keep is
useful for future retrieval.

# Input

You receive a JSON object with:
- trigger, drift_event_id
- drift_event: the full drift_event object (per contracts.md §3.2.2)
- affected_citations_summary: { total_affected, central_count,
  comparative_count, peripheral_count }

# Step 1 — retrieve candidate patterns (mandatory)

Call `search_drift_patterns` exactly once with:
- query_text = drift_event.drift_summary
- top_k = 5
- Do NOT pass min_score (the retriever's score is rank-based, not a relevance
  signal).

# Step 2 — decide create vs update YOURSELF

For each retrieved pattern, read its `pattern_description` carefully and judge:
"does this pattern describe the same underlying phenomenon as the new
drift_event?" The retriever's `similarity_score` is NOT a reliable signal —
ignore it.

Criteria for a true match (must be true for all three):
- Same broad domain (e.g. both COVID-clinical, both physics-cosmology — not
  one COVID and one cosmology).
- Same drift_type (a `numerical_shift`-flavoured drift should not be merged
  into an `hedging_addition` pattern).
- Compatible magnitude / direction (e.g. both describe large effect-size
  reductions; one describing a 5% wobble does not match a 70% collapse).

# Step 3 — call EXACTLY ONE write tool

If one retrieved pattern is a true match (most often there will be at most
one; if there are multiple, pick the one whose description is closest):
- Call `update_drift_pattern(pattern_id=<that pattern's id>,
  source_event_id=drift_event.event_id)`.
- v0 update is append-evidence-only — do NOT try to refine
  `pattern_description` or add domain tags this round (this is intentional;
  see §3.5.1 TODO).

If NO retrieved pattern is a true match (or the retrieved list is empty):
- Call `create_drift_pattern(...)` with:
  - pattern_description: a REUSABLE summary — what this kind of drift looks
    like in general, not what THIS one event did. Include domain + drift type
    + rough magnitude. Do NOT include DOIs, author names, or one-off
    specifics. 30-80 words.
    Good: "COVID-related clinical preprints often show large (>30%)
    reductions in reported drug-efficacy effect sizes at publication, often
    accompanied by added hedging."
    Bad: "Hydroxychloroquine paper by Smith et al. went from 45% to 12%."
  - pattern_type: one of `numerical_softening` | `hedging_addition` |
    `claim_disappearance` | `effect_size_reduction` | `other`.
  - domain_tags: 3-6 lowercase-hyphen tags mixing broad + specific (e.g.
    `["covid-19", "clinical-trial", "rct", "antiviral"]`).
  - source_event_id: drift_event.event_id.

Call only ONE write tool. Do not call both, and do not call neither.

# Step 4 — return the response

After the write tool returns, emit a JSON object matching contracts.md §3.5.2,
populating `pattern` from the tool's return value verbatim:

{
  "action": "create_new" | "update_existing",
  "pattern": { /* the dict returned by the write tool */ },
  "synthesized_at": null
}

Leave `synthesized_at` as null — like Drift Analyzer's `analyzed_at`, this is
filled in by the orchestrating workflow, not invented by you.
"""

root_agent = LlmAgent(
    name="memory_synthesizer",
    model=MODEL_PRO,
    description="Distills drift_events into reusable, retrievable drift patterns (memory loop write side).",
    instruction=INSTRUCTION,
    tools=[elastic_mcp_tools],
)
