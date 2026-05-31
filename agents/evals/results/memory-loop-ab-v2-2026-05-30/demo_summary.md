# Memory Loop A/B v2 Demo Summary

## Result

- Baseline materiality: `0.75`
- Treatment materiality: `1.0`
- Calibration delta: `0.25`
- Negative-control materiality: `0.0`
- Memory pattern used for calibration: `["memory-loop-v2-outcome-switch-0101"]`

## Memory Artifact

- pattern_id: `memory-loop-v2-outcome-switch-0101`
- pattern_type: `outcome_switch`
- support_count: `1`

## Demo Beats

1. Show `baseline.json`: no retrieved memory, materiality stays below max severity.
2. Show `memory_raw.json` then `memory.json`: LLM proposes semantics; code owns ids/timestamps/schema.
3. Show `treatment.json`: retrieved outcome-switch memory calibrates severity upward.
4. Show `negative.json`: cosmetic/unit copy-edit does not misuse outcome-switch memory.
5. Show `score.txt`: strict scorer verdict.
