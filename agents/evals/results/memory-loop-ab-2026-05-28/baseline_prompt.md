Run Drift Analyzer on the following case.

Important:
- This is the BASELINE run.
- Do NOT use memory retrieval.
- Do NOT call search_drift_patterns.
- retrieved_patterns_used must be [].
- Return JSON only.

Input:
{
  "preprint_doi": "10.1101/eval.treatment.0002",
  "preprint_version_compared": "v3",
  "published_doi": "10.1000/eval.treatment.0002",
  "preprint_claims": [
    {
      "claim_id": "10.1101/eval.treatment.0002::v3::abstract::0",
      "text": "The AI triage system reached an AUROC of 0.96 and reduced false-negative referrals by 38% in a multicenter validation study.",
      "claim_type": "quantitative",
      "hedging_level": "none"
    },
    {
      "claim_id": "10.1101/eval.treatment.0002::v3::abstract::1",
      "text": "The system generalized robustly across hospital sites and demographic groups.",
      "claim_type": "qualitative",
      "hedging_level": "none"
    }
  ],
  "published_claims": [
    {
      "claim_id": "10.1000/eval.treatment.0002::published::abstract::0",
      "text": "The final article presents the AI triage system as exploratory and omits the AUROC, false-negative reduction, and demographic subgroup generalization claims from the abstract.",
      "claim_type": "qualitative",
      "hedging_level": "strong"
    }
  ]
}

