Run Drift Analyzer on the following case.

Important:
- Use memory retrieval normally.
- Only use a pattern if domain, drift type, and phenomenon are relevant.
- Do not use primary-outcome-switch memory for a cosmetic copy-edit or unit-formatting case.
- Return severity_calibration; if no memory is relevant, memory_pattern_ids and evidence must be [].
- Return JSON only.

Input:
{
  "preprint_doi": "10.1101/eval.v2.negative.0103",
  "preprint_version_compared": "v1",
  "published_doi": "10.1000/eval.v2.negative.0103",
  "preprint_claims": [
    {
      "claim_id": "10.1101/eval.v2.negative.0103::v1::abstract::0",
      "text": "The device reduced average systolic blood pressure by 4.8 mm Hg over eight weeks, while the prespecified primary endpoint remained change in systolic blood pressure.",
      "claim_type": "quantitative",
      "hedging_level": "none"
    }
  ],
  "published_claims": [
    {
      "claim_id": "10.1000/eval.v2.negative.0103::published::abstract::0",
      "text": "The device reduced mean systolic blood pressure by 4.8 mmHg over 8 weeks; the prespecified primary endpoint was change in systolic blood pressure.",
      "claim_type": "quantitative",
      "hedging_level": "none"
    }
  ]
}
