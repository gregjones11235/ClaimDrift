"""
Claim Extractor — sentence-level biomedical claim extraction.

Owns:    contracts.md §3.1 Claim Extractor input/output
Reads:   nothing (gets fed via Elastic Workflows from `preprints` index)
Writes:  `claims` index (via the orchestrator, not directly)
Tools:   none (v0); A may add hedging-lexicon lookup later
"""

from google.adk.agents import LlmAgent

# Inlined from agents/_shared/config.py:MODEL_FLASH. The Agent Engine deploy
# packages only this subdirectory, so the `_shared` sibling package is not
# importable in the deployed runtime. Keep this constant in sync with
# _shared/config.py if it ever changes (it hasn't since v0). See
# agents/_DEPLOY_CHECKLIST.md Pitfall #1.
MODEL_FLASH = "gemini-2.5-flash"

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
  value MUST be a JSON number, not a string. Write 45, 45.0, or 0.045 —
  NEVER "45" with quotes. Downstream drift_analyzer computes deltas as
  (b - a) / a; a quoted string will throw at division time.
  comparison must be one of: "reduction", "increase", "ratio", "absolute"

Return ONLY a JSON object of the form:
{
  "preprint_doi": "<echoed from the user message>",
  "version": "<echoed from the user message>",
  "claims": [ ... ],
  "extraction_metadata": {
    "model": "gemini-2.5-flash",
    "extracted_at": null
  }
}

Leave `extraction_metadata.extracted_at` as null — like `analyzed_at` in
§3.2.2, `drafted_at` in §3.4.2, and `synthesized_at` in §3.5.2, this is a
wall-clock field filled in by the orchestrating Cloud Run dispatcher
(§9.6.1) when it receives your output. LLMs cannot read a real clock, and
an invented timestamp is worse than null because it looks real.

Do not include any prose outside the JSON.
"""

root_agent = LlmAgent(
    name="claim_extractor",
    model=MODEL_FLASH,
    description="Extracts sentence-level scientific claims from a biomedical preprint.",
    instruction=INSTRUCTION,
)
