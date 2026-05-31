Run Memory Synthesizer on the following drift event.

Important:
- Return JSON only.
- Do NOT call any tool.
- Do NOT write Python code.
- Do NOT use import, print, datetime, uuid, or default_api.
- Create or update a reusable drift pattern if appropriate.
- This is a v2 seed case for outcome_switch / primary endpoint demotion.
- The pattern_type must be "outcome_switch".
- Use pattern_id: "memory-loop-v2-outcome-switch-0101".
- Use source_event_ids: ["memory-loop-v2-seed-0101"].
- Use support_count: 1.
- Use synthesized_at: null.
- Use created_at: null and last_updated_at: null.
- The pattern should describe primary efficacy endpoints being demoted to exploratory, secondary, safety, feasibility, or adherence framing at publication, with strong efficacy claims becoming hedged.

Input:
{
  "event_id": null,
  "preprint_doi": "10.1101/eval.v2.seed.0101",
  "preprint_version_compared": "v2",
  "published_doi": "10.1000/eval.v2.seed.0101",
  "drift_summary": "The preprint's primary efficacy endpoint, 12-month progression-free survival, was demoted to an exploratory secondary analysis in the published version, replaced by treatment feasibility and adverse-event characterization as primary outcomes. Additionally, the strong conclusion about the regimen's efficacy as a benchmark was significantly hedged.",
  "claim_diffs": [
    {
      "diff_type": "outcome_switch",
      "preprint_claim_id": "10.1101/eval.v2.seed.0101::v2::abstract::0",
      "published_claim_id": "10.1000/eval.v2.seed.0101::published::abstract::0",
      "preprint_text": "The randomized phase II trial met its primary endpoint, with the investigational therapy improving 12-month progression-free survival from 41% to 63% in metastatic pancreatic cancer.",
      "published_text": "The final article identifies treatment feasibility and adverse-event characterization as the primary outcomes; progression-free survival is reported as an exploratory secondary analysis and is not described as meeting the primary endpoint.",
      "change_description": "The trial's primary endpoint shifted from progression-free survival (an efficacy outcome) to treatment feasibility and adverse-event characterization (safety/tolerability outcomes), with the original efficacy outcome demoted to an exploratory secondary analysis and explicitly stated as not meeting the primary endpoint. This represents a fundamental change in the study's main findings and interpretation of success."
    },
    {
      "diff_type": "hedging_added",
      "preprint_claim_id": "10.1101/eval.v2.seed.0101::v2::abstract::1",
      "published_claim_id": "10.1000/eval.v2.seed.0101::published::abstract::1",
      "preprint_text": "The regimen should be considered a new efficacy benchmark for patients after first-line therapy.",
      "published_text": "The regimen may warrant further evaluation in selected patients.",
      "change_description": "A strong, definitive claim that the regimen constitutes a 'new efficacy benchmark' was softened to a highly cautious statement suggesting it 'may warrant further evaluation', indicating a significant reduction in the confidence attributed to the efficacy findings."
    }
  ],
  "materiality_score": 0.9,
  "severity_calibration": {
    "baseline_materiality_without_memory": 0.9,
    "calibrated_materiality": 0.9,
    "calibration_delta": 0.0,
    "memory_pattern_ids": [],
    "evidence": [],
    "rationale": "No genuinely relevant prior drift patterns specifically related to outcome_switch or primary endpoint demotion were retrieved from memory to inform severity calibration. The high materiality score reflects the fundamental change in the primary outcomes and the significant hedging of efficacy claims, judged based on the direct comparison of the claims themselves."
  },
  "retrieved_patterns_used": [],
  "analyzed_at": null
}
