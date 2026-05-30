# Memory Loop A/B Evidence - 2026-05-28

This directory captures one reproducible memory-loop A/B run used for prompt
and retrieval-query tuning.

## Result

`score.txt` verdict: PASS.

The run verifies:

- baseline output did not use retrieved memory;
- `memory_synthesizer` updated the expected diagnostic AI claim-disappearance
  pattern;
- treatment output used the expected pattern
  `c31f4560-611c-4335-a7a2-97b744d014da`;
- treatment output explained memory influence;
- negative control did not use the diagnostic AI pattern.

## Key Finding

The first treatment attempts showed that prompt-only relevance filtering was not
enough when the retrieval query was built only from preprint claims. The expected
diagnostic AI pattern did not appear in the top retrieved candidates.

The successful run changed Drift Analyzer retrieval guidance so
`search_drift_patterns` query text includes:

1. preprint claim text;
2. published claim text;
3. an inferred drift/domain hint, for example
   `AI diagnostic tool claim_disappearance quantitative performance metrics removed`.

With that query construction, the expected pattern was retrieved as the top
candidate and then used in `retrieved_patterns_used`.

## Captured Artifacts

- `baseline.json`: no-memory treatment baseline.
- `seed_drift_event.json`: seed diagnostic claim-disappearance drift event.
- `memory.json`: Memory Synthesizer update of the expected pattern.
- `treatment.json`: tuned treatment output using the expected pattern.
- `negative.json`: agriculture/yield negative control.
- `score.txt`: automated scoring output.

## Caveat

The captured treatment output includes `materiality_score` inside individual
`claim_diffs`. This does not affect the memory-loop A/B verdict, but the Drift
Analyzer prompt was tightened afterward so `materiality_score` belongs only at
the top level of the drift report.
