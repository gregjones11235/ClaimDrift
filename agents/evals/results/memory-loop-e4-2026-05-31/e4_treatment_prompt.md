Run Drift Analyzer on the following case.

Important:
- Use memory retrieval normally.
- When calling search_drift_patterns, build query_text from preprint claims, published claims, and a STRUCTURED drift descriptor inferred from THIS case (domain + study type + drift type + magnitude/direction). Do NOT use any hard-coded AI-diagnostic hint.
- This is an economics effect-size-reduction case, NOT a clinical outcome switch.
- Perform a relevance audit: only list a pattern in retrieved_patterns_used if its domain, drift type, and phenomenon genuinely match THIS economics case.
- Do NOT use the clinical primary-outcome-switch memory for this economics case.
- Return severity_calibration; if the economics effect_size_reduction memory is relevant, use its support_count to calibrate. If no memory is relevant, set memory_pattern_ids and evidence to [] and calibration_delta to 0.0.
- Do not put materiality_score inside individual claim_diffs.
- Return JSON only.

Input:
{
  "preprint_doi": "10.1101/eval.e4.econ.0001",
  "preprint_version_compared": "v2",
  "published_doi": "10.1000/eval.e4.econ.0001",
  "preprint_claims": [
    {
      "claim_id": "10.1101/eval.e4.econ.0001::v2::abstract::0",
      "text": "A minimum-wage increase of 10% reduced teen employment by 6.2% (p<0.01) in our two-way fixed-effects specification across 48 states.",
      "claim_type": "quantitative",
      "hedging_level": "none"
    },
    {
      "claim_id": "10.1101/eval.e4.econ.0001::v2::abstract::1",
      "text": "These results provide robust causal evidence of large disemployment effects.",
      "claim_type": "comparative",
      "hedging_level": "none"
    }
  ],
  "published_claims": [
    {
      "claim_id": "10.1000/eval.e4.econ.0001::published::abstract::0",
      "text": "After adding county-level controls and a border-discontinuity robustness check, a 10% minimum-wage increase is associated with a 1.1% reduction in teen employment, not statistically significant at conventional levels (p=0.21).",
      "claim_type": "quantitative",
      "hedging_level": "moderate"
    },
    {
      "claim_id": "10.1000/eval.e4.econ.0001::published::abstract::1",
      "text": "The evidence for a disemployment effect is weaker than initially reported.",
      "claim_type": "comparative",
      "hedging_level": "moderate"
    }
  ]
}
