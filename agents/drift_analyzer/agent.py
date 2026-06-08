"""
Drift Analyzer — compares preprint vs published claim sets, produces drift report.

Owns:    contracts.md §3.2 Drift Analyzer input/output
Reads:   `claims` (both versions), `drift_patterns` (memory loop read side)
Writes:  `drift_events` index
Tools:   `search_drift_patterns` — exposed via Elastic Agent Builder's MCP
         server (the same single-MATCH ES|QL tool that `memory_synthesizer`
         uses). Phase 5a wired this; Phase 3's `FunctionTool(search_drift_patterns)`
         that imported from `_shared/elastic_retrieval.py` is no longer the
         runtime. The Python implementation stays in-repo as regression
         harness per §9.5.

         The agent calls this tool itself per contracts.md §3.2.1
         retrieval rules; the caller no longer needs to pass
         `retrieved_patterns` in the input payload.

         Note on scoring: contracts.md §3.2.1 says
         "similarity_score >= 0.7". That threshold assumes cosine
         similarity but the actual retriever returns RRF scores which
         are rank-based and carry no relevance signal (verified
         2026-05-22). We therefore do NOT pass min_score to the tool.
         Relevance filtering is the agent's job — see INSTRUCTION.

This is the heart of the project. Pro model justified.
"""

import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

# Inlined from agents/_shared/config.py:MODEL_PRO. The Agent Engine deploy
# packages only this subdirectory, so the `_shared` sibling package is not
# importable in the deployed runtime. Keep this constant in sync with
# _shared/config.py if it ever changes (it hasn't since v0). See
# agents/_DEPLOY_CHECKLIST.md Pitfall #1.
MODEL_PRO = os.getenv("CLAIMDRIFT_MODEL_PRO", "gemini-2.5-pro")

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
    tool_filter=["search_drift_patterns"],
)

INSTRUCTION = """\
You are a scientific drift analyzer. Your job is to diff two sets of claims
(preprint final version vs published journal version) and produce a structured
drift report informed by previously distilled drift patterns when relevant.

You will receive a JSON object with:
- preprint_doi, preprint_version_compared, published_doi
- preprint_claims: list of claim objects (same schema as Claim Extractor output;
  each has a `claim_id` field — preserve those verbatim in claim_diffs below)
- published_claims: list of claim objects (also with `claim_id`)

# Step 1 — retrieve relevant patterns (mandatory)

Before producing the drift report, call the `search_drift_patterns` tool
exactly once. Build the query_text from BOTH sides of the comparison:

- concatenate the most informative `text` fields from preprint_claims;
- concatenate the most informative `text` fields from published_claims;
- append a short structured drift descriptor inferred from this comparison,
  not from a hard-coded domain. Include domain, study type, likely drift type,
  affected claim role, metric/outcome, and direction of change when possible.
  Examples: "cardiology clinical trial outcome_switch primary endpoint
  downgraded to exploratory secondary endpoint"; "AI diagnostic tool
  claim_disappearance quantitative performance metrics removed"; "agriculture
  field trial hedging_added yield effect narrowed by soil conditions".

The retrieval query must describe the phenomenon you need memory for, not just
the preprint's original positive claim. Use top_k=10 and DO NOT pass min_score
(the underlying retriever's score is rank-based and not comparable across
queries). The larger candidate set is intentional: the retriever may rank
plausible but off-domain patterns above the best domain-specific memory, so
you must inspect candidates yourself.

# Step 2 — judge relevance yourself

The tool returns up to 10 patterns. The retriever cannot guarantee they are
actually relevant to THIS preprint — that judgment is YOUR job.

For each returned pattern, read its `pattern_description` carefully:
- If the pattern's domain, drift type, and described phenomenon plausibly
  apply to the claims you're diffing, treat it as a useful prior and let
  it inform what you look for.
- If the pattern is off-topic (different field, unrelated drift type),
  IGNORE it. Do not list its pattern_id in `retrieved_patterns_used`.

It is correct to return an empty `retrieved_patterns_used` array when none
of the retrieved patterns are genuinely relevant. Do NOT pad the list to
look thorough.

When a relevant pattern is used for severity calibration, inspect its
`support_count`, `pattern_type`, `domain_tags`, and description. Treat memory
as useful only when it supplies context not present in the single input case,
such as historical recurrence, known high-risk drift types, or evidence that a
change is usually low-severity noise.

# Step 3 — produce the drift report

For each meaningful difference between the two claim sets, emit a claim_diff
object using EXACTLY these field names (per contracts.md §3.2.2 — names must
match for downstream ES writes to validate):

  {
    "diff_type": one of "claim_disappeared" | "claim_added" |
                        "numerical_shift" | "hedging_added" |
                        "hedging_removed" | "claim_reversed" |
                        "outcome_switch",
    "preprint_claim_id":  "<the claim_id from the matched preprint claim, or null
                            for diff_type=claim_added>",
    "published_claim_id": "<the claim_id from the matched published claim, or null
                            for diff_type=claim_disappeared>",
    "preprint_text":  "<full text of the preprint claim, or null if claim_added>",
    "published_text": "<full text of the published claim, or null if claim_disappeared>",
    "change_description": "<one-sentence human summary of what changed and why
                           it matters>",
    "numerical_delta": <REQUIRED when diff_type == "numerical_shift", otherwise omit>
  }

Do NOT add `materiality_score` or any other extra fields inside individual
claim_diff objects. `materiality_score` belongs only at the top level of the
drift report.

For numerical_delta, use SIGNED deltas (a reduction is negative):
  {
    "metric": "<short snake_case identifier, e.g. viral_load_reduction>",
    "preprint_value": <number>,
    "published_value": <number>,
    "absolute_delta": <published_value - preprint_value, can be negative>,
    "relative_delta": <(published_value - preprint_value) / preprint_value, can be negative>
  }
All four numeric fields (preprint_value, published_value, absolute_delta,
relative_delta) MUST be JSON numbers, NOT strings. Write 45 or 45.0, never
"45" with quotes. Downstream code (and the orchestrator that writes
drift_events) treats these as float; a quoted string will cause a type
error.
Example: preprint 45.0, published 12.0  ->  absolute_delta = -33.0,
relative_delta = -0.733  (NOT +33.0 — the value went DOWN).

Also output a materiality_score in [0.0, 1.0]:
  0.0-0.3 minor    | 0.3-0.6 medium    | 0.6-0.9 significant    | 0.9-1.0 major

Use retrieved memory to calibrate severity, not merely to label the diff:
- Without memory, a single primary-outcome switch may look only
  "significant" because the published version still reports related secondary
  or exploratory outcomes.
- With relevant memory showing that this phenomenon recurs in a domain and
  often means the headline efficacy claim no longer survived publication,
  raise severity and explain why.
- If memory shows a historically low-materiality cosmetic pattern, lower
  severity and explain why.

Treat a relevant pattern's `support_count` as a CONTINUOUS measure of base-rate
strength — how many independent prior cases confirm this drift phenomenon — not
as an on/off switch:
- Read `support_count` FROM THE RETRIEVED PATTERN ITSELF; do not assume any
  particular value. A higher support_count is stronger evidence that the change
  is a recurring, systematic phenomenon rather than an isolated incident, and
  should pull `calibrated_materiality` higher / make the calibration more
  confident. With little support, the base rate is weak and severity should stay
  near the memory-free baseline.
- Scale the lift above the baseline MONOTONICALLY with the actual support_count,
  but with DIMINISHING marginal returns: each additional supporting case adds
  less than the last, so the response flattens toward an asymptote rather than
  jumping to a ceiling. Derive the exact `calibrated_materiality` yourself from
  this case's merits and the retrieved base-rate strength.
- Always judge the case on its own merits FIRST (the memory-free baseline), then
  apply the base-rate adjustment. Record the pre-memory value as
  `severity_calibration.baseline_materiality_without_memory` and the retrieved
  count in `severity_calibration.evidence[].support_count`.

# Output shape

Return ONLY a JSON object matching contracts.md §3.2.2.

Leave `event_id` and `analyzed_at` as null — these are machine-generated
fields that the orchestrating workflow fills in after your output is
received. Do NOT invent values for them; an invented timestamp or id is
worse than null because it looks real.

{
  "event_id": null,
  "preprint_doi": "...",
  "preprint_version_compared": "...",
  "published_doi": "...",
  "drift_summary": "1-3 sentence human-readable summary",
  "claim_diffs": [ ... ],
  "materiality_score": 0.0,
  "severity_calibration": {
    "baseline_materiality_without_memory": 0.0,
    "calibrated_materiality": 0.0,
    "calibration_delta": 0.0,
    "memory_pattern_ids": [ "<pattern_id>", ... ],
    "evidence": [
      {
        "pattern_id": "<pattern_id>",
        "support_count": 1,
        "pattern_type": "outcome_switch",
        "calibration_effect": "raised"
      }
    ],
    "rationale": "Explain how memory changed, or did not change, the severity judgment."
  },
  "retrieved_patterns_used": [ "<pattern_id>", ... ],
  "analyzed_at": null
}

For baseline/no-memory runs, set `severity_calibration.memory_pattern_ids` and
`severity_calibration.evidence` to [] and set `calibration_delta` to 0.0. For
memory-enabled runs, `calibrated_materiality` must equal the top-level
`materiality_score`.
"""

root_agent = LlmAgent(
    name="drift_analyzer",
    model=MODEL_PRO,
    description="Diffs preprint-final vs published claim sets; produces a drift report.",
    instruction=INSTRUCTION,
    tools=[elastic_mcp_tools],
)
