Run Drift Analyzer on the following case.

Important:
- This is the SEED run for a new v2 memory pattern.
- Use search_drift_patterns only to check whether a clearly relevant outcome_switch / primary endpoint demotion pattern already exists.
- If retrieved candidates are about claim disappearance, generic hedging, psychology, pharmacology, or effect-size reduction rather than primary outcome / endpoint switching, ignore them.
- Do NOT list weakly related retrieved patterns in retrieved_patterns_used.
- It is acceptable for retrieved_patterns_used to be [] if no true outcome_switch memory exists yet.
- Focus the drift report on outcome_switch and primary endpoint demotion.
- If no true outcome_switch memory is used, set severity_calibration.memory_pattern_ids and severity_calibration.evidence to [] and calibration_delta to 0.0.
- Return JSON only.

Input:
{
  "preprint_doi": "10.1101/eval.v2.seed.0101",
  "preprint_version_compared": "v2",
  "published_doi": "10.1000/eval.v2.seed.0101",
  "preprint_claims": [
    {
      "claim_id": "10.1101/eval.v2.seed.0101::v2::abstract::0",
      "text": "The randomized phase II trial met its primary endpoint, with the investigational therapy improving 12-month progression-free survival from 41% to 63% in metastatic pancreatic cancer.",
      "claim_type": "quantitative",
      "hedging_level": "none"
    },
    {
      "claim_id": "10.1101/eval.v2.seed.0101::v2::abstract::1",
      "text": "The regimen should be considered a new efficacy benchmark for patients after first-line therapy.",
      "claim_type": "qualitative",
      "hedging_level": "none"
    }
  ],
  "published_claims": [
    {
      "claim_id": "10.1000/eval.v2.seed.0101::published::abstract::0",
      "text": "The final article identifies treatment feasibility and adverse-event characterization as the primary outcomes; progression-free survival is reported as an exploratory secondary analysis and is not described as meeting the primary endpoint.",
      "claim_type": "qualitative",
      "hedging_level": "strong"
    },
    {
      "claim_id": "10.1000/eval.v2.seed.0101::published::abstract::1",
      "text": "The regimen may warrant further evaluation in selected patients.",
      "claim_type": "qualitative",
      "hedging_level": "strong"
    }
  ]
}
