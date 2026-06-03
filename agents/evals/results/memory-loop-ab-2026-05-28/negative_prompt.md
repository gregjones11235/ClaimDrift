Run Drift Analyzer on the following case.

Important:
- Use memory retrieval normally.
- Only use a pattern if domain, drift type, and phenomenon are relevant.
- Do not use diagnostic-tool memory for an agriculture/yield case.
- Return JSON only.

Input:
{
  "preprint_doi": "10.1101/eval.negative.0003",
  "preprint_version_compared": "v1",
  "published_doi": "10.1000/eval.negative.0003",
  "preprint_claims": [
    {
      "claim_id": "10.1101/eval.negative.0003::v1::abstract::0",
      "text": "The seed treatment increased soybean yield by 12% across all field sites.",
      "claim_type": "quantitative",
      "hedging_level": "none"
    }
  ],
  "published_claims": [
    {
      "claim_id": "10.1000/eval.negative.0003::published::abstract::0",
      "text": "The seed treatment may increase soybean yield under selected soil conditions, although effects varied across sites.",
      "claim_type": "quantitative",
      "hedging_level": "strong"
    }
  ]
}

