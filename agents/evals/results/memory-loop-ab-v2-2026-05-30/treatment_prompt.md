Run Drift Analyzer on the following case.

Important:
- Use memory retrieval normally.
- When calling search_drift_patterns, build query_text from preprint claims, published claims, and a structured drift descriptor inferred from this case, e.g. domain + clinical trial + outcome_switch + primary endpoint demoted to exploratory/secondary endpoint.
- Do not use a hard-coded AI diagnostic hint.
- Before finalizing retrieved_patterns_used, perform a relevance audit.
- Inspect all returned candidates, not only the highest-ranked one.
- Prefer patterns whose drift type matches outcome_switch or primary endpoint demotion.
- Use relevant pattern support_count and domain recurrence to calibrate severity.
- Return severity_calibration with baseline_materiality_without_memory, calibrated_materiality, calibration_delta, memory_pattern_ids, evidence, and rationale.
- If memory materially changes severity, make top-level materiality_score equal severity_calibration.calibrated_materiality.
- Do not put materiality_score inside individual claim_diffs; materiality_score belongs only at the top level.
- Return JSON only.

Input:
{
  "preprint_doi": "10.1101/eval.v2.treatment.0102",
  "preprint_version_compared": "v3",
  "published_doi": "10.1000/eval.v2.treatment.0102",
  "preprint_claims": [
    {
      "claim_id": "10.1101/eval.v2.treatment.0102::v3::abstract::0",
      "text": "The multicenter trial achieved its primary clinical endpoint, reducing 90-day heart-failure readmission from 22% to 13% compared with usual care.",
      "claim_type": "quantitative",
      "hedging_level": "none"
    },
    {
      "claim_id": "10.1101/eval.v2.treatment.0102::v3::abstract::1",
      "text": "These findings support immediate adoption of the remote titration protocol across cardiology clinics.",
      "claim_type": "qualitative",
      "hedging_level": "none"
    }
  ],
  "published_claims": [
    {
      "claim_id": "10.1000/eval.v2.treatment.0102::published::abstract::0",
      "text": "The published article defines medication-adherence documentation as the primary outcome; 90-day readmission is reported only as an exploratory secondary endpoint and is not presented as statistically significant.",
      "claim_type": "qualitative",
      "hedging_level": "strong"
    },
    {
      "claim_id": "10.1000/eval.v2.treatment.0102::published::abstract::1",
      "text": "The protocol may be useful for workflow redesign but requires confirmation in a dedicated outcomes trial.",
      "claim_type": "qualitative",
      "hedging_level": "strong"
    }
  ]
}
