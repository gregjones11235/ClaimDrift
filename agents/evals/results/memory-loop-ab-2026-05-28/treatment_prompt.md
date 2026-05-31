Run Drift Analyzer on the following case.

Important:
- Use memory retrieval normally.
- When calling search_drift_patterns, build query_text from preprint claims, published claims, and an inferred hint: AI diagnostic tool claim_disappearance quantitative performance metrics removed.
- Before finalizing retrieved_patterns_used, perform a relevance audit.
- Inspect all returned candidates, not only the highest-ranked one.
- Prefer patterns whose domain matches AI / machine learning / diagnostic tools.
- Prefer patterns whose drift type matches claim_disappearance.
- Prefer patterns describing quantitative performance metrics disappearing from preprint to publication.
- Do NOT use pharmacology, biochemistry, clinical genetics, hedging-addition, or effect-size-reduction patterns unless they directly match this case.
- If a retrieved pattern is relevant, include its pattern_id in retrieved_patterns_used and explain how it affected your reasoning.
- Do not put materiality_score inside individual claim_diffs; materiality_score belongs only at the top level.
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

