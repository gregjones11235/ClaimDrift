"""
Citation Finder — finds downstream papers citing the drifted preprint
                  and scores severity per citing paper.

Owns:    contracts.md §3.3 Citation Finder input/output
Reads:   OpenAlex citation edges (via openalex_puller tool in v1)
Writes:  `affected_citations` index
Tools:   v0 has none — the agent FABRICATES 2-3 plausible citing papers
         for smoke-test purposes only. v1 will plug in an OpenAlex
         lookup tool (B will provide the puller; we wire it as an MCP tool).

WARNING — v0 produces fake data. Do not commit any v0 output to the
`affected_citations` index. Mark v0 results clearly in any demo.
"""

from google.adk.agents import LlmAgent
from _shared.config import MODEL_FLASH

INSTRUCTION = """\
You are a citation impact analyzer.

You will receive a drift_event containing:
- drift_event_id, preprint_doi
- drift_summary: human-readable summary of what changed
- claim_diffs: list of specific claim differences

YOUR JOB IN V0: fabricate 2-3 plausible downstream papers that might cite
this preprint, and score how affected each one is by the drift. This is a
smoke-test placeholder — when the real OpenAlex tool is wired in (v1),
this prompt will change to: "look up real citations and score them".

For each fabricated citing paper, output:
- citing_paper_doi: plausible-looking DOI (use 10.1038/..., 10.1016/..., etc)
- citing_paper_title: plausible title in the same biomedical domain
- citing_paper_authors: 1-2 fake authors with name and null orcid/email
- citation_context: null
- severity_tier: one of "central", "comparative", "peripheral"
- severity_reasoning: 1-2 sentences justifying the tier

Do not return severity_tier="pending". Pending is reserved for raw
OpenAlex candidates before this agent scores them.

Severity guide:
- central:     the drifted claim is the citing paper's main argument
- comparative: the drifted claim is used as comparison/reference, not core
- peripheral: the drifted claim is mentioned once in related work

Return ONLY a JSON object matching contracts.md §3.3.2:
{
  "drift_event_id": "...",
  "affected_citations": [ ... ],
  "total_found": <int>,
  "processed": <int>,
  "found_at": "<ISO 8601 UTC>"
}
"""

root_agent = LlmAgent(
    name="citation_finder",
    model=MODEL_FLASH,
    description="Finds papers citing the drifted preprint and scores severity per citation.",
    instruction=INSTRUCTION,
)
