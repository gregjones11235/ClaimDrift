# Memory Loop A/B v2 Run - Severity Calibration

Generated from `agents/evals/memory_loop_ab_cases.json` using case suite `v2`.

This directory is intentionally separate from the v1 evidence in
`agents/evals/results/memory-loop-ab-2026-05-28/`.

v1 proves the memory loop can write, retrieve, and use a pattern. v2 raises the
bar: memory must change the severity/materiality calibration in a measurable
way.

## Scenario

The v2 flagship fixture is `outcome_switch`:

- seed: oncology clinical trial primary efficacy endpoint is demoted to
  feasibility/adverse-event framing;
- treatment: cardiology clinical trial primary readmission endpoint is demoted
  to exploratory secondary endpoint;
- negative: same broad cardiology domain, but only cosmetic wording/unit
  formatting, so outcome-switch memory should not be used.

## Artifacts To Fill

Fill these files with captured agent JSON outputs:

- `baseline.json`
- `seed_drift_event.json`
- `memory.json`
- `treatment.json`
- `negative.json`
- `score.txt`

Do not reuse v1 JSON outputs here. This directory should contain only v2
severity-calibration evidence.

## Prompt Files

Prompt files:

- `baseline_prompt.md`: send to a no-memory LLM or no-memory Drift Analyzer.
- `seed_drift_analyzer_prompt.md`: send to `drift_analyzer`.
- `memory_synthesizer_prompt.md`: paste `seed_drift_event.json`, then send to `memory_synthesizer`.
- `treatment_prompt.md`: send to `drift_analyzer`.
- `negative_prompt.md`: send to `drift_analyzer`.

## Success Criteria

The run should pass the normal memory-loop checks and the stricter v2 checks:

- baseline does not use memory;
- memory_synthesizer creates or updates an `outcome_switch` pattern;
- treatment retrieves and uses relevant outcome-switch memory;
- treatment includes `severity_calibration`;
- treatment materiality exceeds baseline by at least `0.15`;
- negative control does not use outcome-switch memory for cosmetic copy-editing;
- no invented machine timestamps/ids, placeholder source_event_ids, or nested
  `claim_diffs[].materiality_score`.

## Scoring

Score after the JSON files are saved:

```bash
python3 agents/scripts/memory_loop_ab_eval.py score-run \
  --run-dir agents/evals/results/memory-loop-ab-v2-2026-05-30 \
  --min-materiality-delta 0.15 \
  --strict-fields \
  | tee agents/evals/results/memory-loop-ab-v2-2026-05-30/score.txt
```
