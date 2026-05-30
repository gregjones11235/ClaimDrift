Run Drift Analyzer on the following case.

Important:
- This is the BASELINE run.
- Do NOT use memory retrieval.
- Do NOT call search_drift_patterns.
- retrieved_patterns_used must be [].
- Score only from the single input case.
- Do NOT use historical recurrence, field-level base rates, or prior memory about how often outcome switches imply failed primary efficacy claims.
- The direct text shows an outcome_switch, so materiality should be significant, but without memory calibration it should not be maximum severity.
- Keep materiality_score in the 0.70-0.80 range unless the input itself shows a full conclusion reversal.
- Return severity_calibration with baseline_materiality_without_memory equal to materiality_score, calibrated_materiality equal to materiality_score, calibration_delta 0.0, memory_pattern_ids [], and evidence [].
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
