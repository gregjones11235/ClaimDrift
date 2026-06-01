Run Drift Analyzer on the following case.

Important:
- Use memory retrieval normally.
- When calling search_drift_patterns, build query_text from preprint claims, published claims, and a STRUCTURED drift descriptor inferred from THIS case (clinical trial outcome_switch: a pre-specified co-primary endpoint demoted to a subordinate step-down endpoint between registration and publication). Do NOT use any hard-coded hint.
- Note this case is deliberately ambiguous: a step-down / hierarchical testing order is itself a legitimate pre-specified design, so from the diff alone the severity is genuinely uncertain (medium). Judge it on its merits first.
- Perform a relevance audit: only list a pattern in retrieved_patterns_used if its domain and drift type genuinely match THIS primary-outcome-switch case.
- If the retrieved outcome_switch pattern (e2-accum-outcome-switch-0001) is relevant, read its support_count FROM THE RETRIEVED PATTERN ITSELF and use that number as the base rate for severity calibration. A higher support_count is stronger evidence that pre-specified primary/co-primary demotion is a recurring phenomenon used to protect a borderline headline result, and should pull calibrated_materiality higher / make the calibration more confident. With little support the base rate is weak and severity should stay near the ambiguous baseline.
- Treat the retrieved support_count as a CONTINUOUS base-rate strength, not an on/off switch: scale the lift above the ambiguous baseline monotonically with the actual support_count you retrieved, but with DIMINISHING marginal returns (each additional supporting case adds less than the last, so the response flattens toward an asymptote rather than jumping to a ceiling). Derive the exact calibrated_materiality yourself from this case's merits and the retrieved base-rate strength. Do NOT assume any particular support_count — use whatever number the retrieval actually returns.
- Return severity_calibration with baseline_materiality_without_memory, calibrated_materiality, calibration_delta, memory_pattern_ids, evidence (include the pattern's support_count), and rationale.
- Make top-level materiality_score equal severity_calibration.calibrated_materiality.
- Do not put materiality_score inside individual claim_diffs.
- Return JSON only.

Input:
{
  "preprint_doi": "registry:NCT01163032:prespecified",
  "preprint_version_compared": "registered_protocol",
  "published_doi": "10.1016/S0140-6736(15)60031-9",
  "preprint_claims": [
    {
      "claim_id": "registry:NCT01163032::prespecified::primary::0",
      "text": "Pre-specified co-primary endpoint 1 (SET): the proportion of patients entrained, assessed from urinary 6-sulphatoxymelatonin (aMT6s) rhythm at month 1, in the intention-to-treat population.",
      "claim_type": "quantitative",
      "hedging_level": "none"
    },
    {
      "claim_id": "registry:NCT01163032::prespecified::primary::1",
      "text": "Pre-specified co-primary endpoint 2 (SET): the proportion of patients with a clinical response, defined as entrainment of aMT6s plus a score of >=3 on the Non-24 Clinical Response Scale (N24CRS).",
      "claim_type": "quantitative",
      "hedging_level": "none"
    }
  ],
  "published_claims": [
    {
      "claim_id": "10.1016/S0140-6736(15)60031-9::published::primary::0",
      "text": "In SET, the primary endpoint was the proportion of entrained patients (aMT6s, month 1): 8/40 (20%) on tasimelteon vs 1/38 (3%) on placebo (difference 17%, 95% CI 3.2-31.6; p=0.0171).",
      "claim_type": "quantitative",
      "hedging_level": "none"
    },
    {
      "claim_id": "10.1016/S0140-6736(15)60031-9::published::stepdown::0",
      "text": "The planned STEP-DOWN primary endpoint assessed the proportion of patients with a clinical response (entrainment plus N24CRS >=3): 9/38 (24%) vs 0/34 (p=0.0028); it is presented as a subordinate step-down endpoint rather than as a co-primary outcome.",
      "claim_type": "quantitative",
      "hedging_level": "moderate"
    }
  ]
}
