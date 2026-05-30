Run Drift Analyzer on the following case.

Important:
- Use memory retrieval normally.
- Return JSON only.

Input:
{
  "preprint_doi": "10.1101/eval.seed.0001",
  "preprint_version_compared": "v2",
  "published_doi": "10.1000/eval.seed.0001",
  "preprint_claims": [
    {
      "claim_id": "10.1101/eval.seed.0001::v2::abstract::0",
      "text": "The diagnostic model achieved 94% sensitivity and 91% specificity for early detection of disease X in an external validation cohort.",
      "claim_type": "quantitative",
      "hedging_level": "none"
    },
    {
      "claim_id": "10.1101/eval.seed.0001::v2::abstract::1",
      "text": "The model outperformed expert clinicians across all evaluated subgroups.",
      "claim_type": "comparative",
      "hedging_level": "none"
    }
  ],
  "published_claims": [
    {
      "claim_id": "10.1000/eval.seed.0001::published::abstract::0",
      "text": "The conference version describes a diagnostic model for disease X but does not report external validation sensitivity, specificity, or subgroup performance claims.",
      "claim_type": "qualitative",
      "hedging_level": "weak"
    }
  ]
}

