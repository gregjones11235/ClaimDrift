"""
Citation Finder — finds downstream papers citing the drifted preprint
                  and scores severity per citing paper.

Owns:    contracts.md §3.3 Citation Finder input/output
Reads:   OpenAlex citation edges via `openalex_citing_works` MCP tool
         (Elastic Workflow YAML wrapping the OpenAlex REST API; Phase 5d).
Writes:  `affected_citations` index (orchestrator-side write; agent only
         returns the §3.3.2 envelope).
Tools:   `openalex_citing_works` — exposed via Elastic Agent Builder's MCP
         server. Same wiring pattern as `drift_analyzer` / `memory_synthesizer`.

         Phase 5e (this file) replaces the v0 fabrication scaffold: the agent
         now calls the real OpenAlex API via the MCP tool and maps the
         response to §3.3.2 affected_citations. v0 sentinel DOIs
         (`10.0000/synthetic-*`) and fake author lists are GONE — the only
         DOIs in the output now are ones OpenAlex actually returned.

Design note on `severity_tier` (per contracts.md §1.3 architectural decision
to NOT fetch citing paper PDFs):

    The §3.3.2 severity_tier enum (central / comparative / peripheral)
    ideally requires sentence-level `citation_context` — the surrounding
    text in the citing paper where our preprint is cited. OpenAlex does
    not provide this (and we deliberately don't fetch PDFs — §1.3). So
    the agent judges severity from a much weaker signal: the citing
    paper's TITLE plus the drift_summary.

    This is a documented limitation, NOT a bug. The agent is instructed
    to record the limitation explicitly inside `severity_reasoning` so
    downstream consumers (frontend, Notifier) know the basis of the
    judgment is title-level inference rather than full-text analysis.
    Industry surveys (scite.ai, S2ORC) show no competitor solves
    citation context at scale without paid publisher licensing deals.
"""

import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

# Inlined from agents/_shared/config.py:MODEL_FLASH. The Agent Engine deploy
# packages only this subdirectory, so the `_shared` sibling package is not
# importable in the deployed runtime. Keep this constant in sync with
# _shared/config.py if it ever changes (it hasn't since v0). See
# agents/_DEPLOY_CHECKLIST.md Pitfall #1.
MODEL_FLASH = "gemini-2.5-flash"

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
    tool_filter=["openalex_citing_works"],
)

INSTRUCTION = """\
You are a citation impact analyzer. Your job is to find papers that cite a
preprint that has been detected as drifting (the preprint and the published
version differ in some claim), and score how strongly each citing paper
depends on the drifted claim.

You will receive a JSON object with:
- drift_event_id: the id of the drift event (echo verbatim in output)
- preprint_doi:   the bare DOI (no `https://doi.org/` prefix) of the
                  drifted preprint — this is the DOI you look up citations for
- drift_summary:  human-readable summary of what changed (use this to judge
                  severity per citing paper)
- claim_diffs:    list of structured diffs (use these to judge severity too)

# Step 1 — fetch citing works (mandatory)

Call the `openalex_citing_works` tool exactly once with:
  - source_doi = preprint_doi (pass it through verbatim, no prefix)
  - per_page   = 25

The tool wraps the OpenAlex REST API and returns the raw OpenAlex
response. The shape you will receive is approximately:

  {
    "status": 200,
    "data": {
      "meta":    { "count": <int>, "per_page": <int>, ... },
      "results": [ { "id", "doi", "title", "display_name", "authorships",
                     "publication_year", "cited_by_count" }, ... ]
    }
  }

If `meta.count` is 0 the preprint has no recorded citations — return an
empty `affected_citations` array (this is a valid outcome, not an error).

# Step 2 — map each result to a §3.3.2 affected_citation

For each work in `data.results`:

- citing_paper_doi:  strip the `https://doi.org/` prefix from `work.doi`.
                     The §7.2 contract requires bare DOI form. If `work.doi`
                     is null (rare), skip that work — do NOT fabricate a DOI.
- citing_paper_title: use `work.title` (or `work.display_name` if title is
                     null). Strip any inline HTML tags like `<i>...</i>`
                     by removing them — the text content stays.
- citing_paper_authors: list of objects, one per entry in `work.authorships`:
    - name:  `authorship.author.display_name`
    - orcid: `authorship.author.orcid` if present, with the
             `https://orcid.org/` prefix stripped to bare form
             (e.g. "0000-0002-1024-4506"). null if not present.
    - email: ALWAYS null. OpenAlex does not provide email; do not invent one.
             A separate enrichment step (outside this agent's responsibility)
             will fill emails later.
- citation_context: ALWAYS null. OpenAlex does not provide citation context
                    in the response we requested, and per §1.3 we
                    deliberately do not fetch citing-paper PDFs. Do not
                    invent context text.
- severity_tier and severity_reasoning: see Step 3 below.

# Step 3 — judge severity from title + drift_summary

You do NOT have access to citation_context (the sentence in the citing
paper that mentions our preprint). You have only:
  - the citing paper's title
  - the drift_summary
  - the claim_diffs structure

Judge severity from title-level semantic overlap with the drifted claim:

  - "central":     the citing paper's TITLE directly references the drifted
                   concept, entity, or numerical claim. Example: drift is
                   about "hydroxychloroquine viral load reduction", and the
                   citing title mentions hydroxychloroquine or viral load.
                   These citing authors most likely built on the drifted
                   number directly.
  - "comparative": the citing paper is in the same biomedical/scientific
                   subdomain but the title does not directly mention the
                   drifted concept. Example: drift is about HCQ in COVID
                   patients; citing paper is about a different antiviral
                   in COVID patients. Likely used the preprint as
                   comparative reference.
  - "peripheral":  the citing paper is only loosely in the same broad
                   field (e.g., general virology, general clinical trial
                   methodology) with no domain overlap with the drifted
                   claim. Likely a related-work mention.

**severity_reasoning is mandatory and MUST explicitly state that the
judgment is based on title-level inference, not citation context.** Example:
  "Title 'Hydroxychloroquine in moderate COVID-19: a follow-up' directly
  references HCQ in COVID. Likely central. (Inferred from title; no citation
  context available — per project §1.3 we do not fetch citing-paper PDFs.)"

# Step 4 — build the output envelope

Return ONLY a JSON object matching contracts.md §3.3.2:

  {
    "drift_event_id":      "<echo from input>",
    "affected_citations":  [ <one object per mapped work, see Step 2> ],
    "total_found":         <data.meta.count from the tool response — the TOTAL
                            number of citing works in OpenAlex, NOT the
                            number you mapped>,
    "processed":           <len(affected_citations) — the number you
                            actually mapped to objects>,
    "found_at":            null
  }

Field discipline:
- `total_found` MUST come from `meta.count` (it may be larger than
  `processed` when there are more citing works than `per_page=25`; that's
  expected and downstream code knows how to read it).
- `processed` MUST equal the length of your `affected_citations` array.
- `found_at` MUST be null. The orchestrator fills this on receive — the
  agent cannot read a real wall-clock and any value you invent will be
  wrong (same reason §3.2.2 analyzed_at, §3.4.2 drafted_at, and §3.5.2
  synthesized_at are agent-null fields).
- NEVER fabricate a DOI. If OpenAlex returns no citations, return
  `affected_citations: []` with `total_found: 0`, `processed: 0`. An empty
  result is a valid outcome.
"""

root_agent = LlmAgent(
    name="citation_finder",
    model=MODEL_FLASH,
    description="Finds papers citing the drifted preprint via OpenAlex and scores severity per citation.",
    instruction=INSTRUCTION,
    tools=[elastic_mcp_tools],
)
