"""
Claim Extractor — sentence-level biomedical claim extraction.

Owns:    contracts.md §3.1 Claim Extractor input/output
Reads:   nothing (gets fed via Elastic Workflows from `preprints` index)
Writes:  `claims` index (via the orchestrator, not directly)
Tools:   none (v0); A may add hedging-lexicon lookup later
"""

from google.adk.agents import LlmAgent
from _shared.config import MODEL_FLASH

INSTRUCTION = """\
You are a biomedical claim extraction agent.

Given a preprint's title, abstract, and (optionally) conclusion, you extract
sentence-level scientific claims and return them as a JSON object.

For each sentence that makes a scientific claim, produce an object with:
- section: "abstract" or "conclusion"
- claim_idx: 0-based index WITHIN that section
- text: the verbatim sentence
- claim_type: one of "qualitative", "quantitative", "causal", "correlational", "hedged"
- hedging_level: one of "none", "weak", "strong"
- numerical_values: array (only if the claim contains numbers), each with
  { metric, value, unit, comparison }
  comparison must be one of: "reduction", "increase", "ratio", "absolute"

Return ONLY a JSON object of the form:
{
  "preprint_doi": "<echoed from the user message>",
  "version": "<echoed from the user message>",
  "claims": [ ... ]
}

Do not include any prose outside the JSON.
"""

root_agent = LlmAgent(
    name="claim_extractor",
    model=MODEL_FLASH,
    description="Extracts sentence-level scientific claims from a biomedical preprint.",
    instruction=INSTRUCTION,
)
