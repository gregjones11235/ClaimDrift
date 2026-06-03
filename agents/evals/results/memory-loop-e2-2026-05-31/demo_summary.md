# Memory Loop v2 — E2 Accumulation Curve Demo Summary

The demo's core shot: as a drift pattern accumulates more supporting evidence
(`support_count`), the Drift Analyzer's `calibrated_materiality` for the same
ambiguous case rises and grows more confident — the memory loop getting sharper
as its base rate accumulates.

## Result (blind run)

`support_count -> calibrated_materiality`, all four tiers run on the SAME fixed
case, prompt identical across tiers (no tier number revealed — see "Blind design"):

| support_count | calibrated_materiality |
|---|---|
| baseline (no memory) | 0.50 |
| 5  | 0.75 |
| 20 | 0.75 |
| 35 | 0.80 |
| 50 | 0.82 |

- Convergence: 0.75 (support=5) → 0.82 (support=50), monotonic, diminishing returns.
- The big step is memory-vs-no-memory (0.50 → 0.75); the further climb (0.75 → 0.82)
  is driven by the accumulating base rate read live from Elasticsearch.

## The case (fixed dependent variable — REAL)

Tasimelteon SET trial for non-24-hour sleep–wake disorder (Lockley et al.,
Lancet 2015;386:1754-64; ClinicalTrials.gov **NCT01163032**), flagged by the
Oxford CEBM **COMPARE** project as `primary_switched=1`.

- **Pre-registered**: TWO co-primary endpoints — (1) aMT6s entrainment at month 1,
  (2) clinical response (entrainment + N24CRS ≥3).
- **Published**: endpoint (1) reported as THE primary; endpoint (2) demoted to a
  subordinate "planned step-down primary endpoint".
- **Why ambiguous**: a step-down / hierarchical testing order is a legitimate
  pre-specified design, so from the diff alone severity is genuinely uncertain
  (baseline 0.50). Only the base rate — that co-primary→step-down demotion recurs
  to protect a borderline headline (here entrainment was just 20% vs 3%, p=0.0171)
  — reveals it as the same outcome-switch phenomenon. That gap is what the memory
  loop closes.

## The base rate (REAL)

Each tier injects N genuine, registered, DOI-bearing outcome-switch cases audited
by COMPARE as the pattern's `source_event_ids`, satisfying the §3.5.1 invariant
`support_count == len(source_event_ids)` at every tier. Case set:
`agents/evals/fixtures/compare_outcome_switch_cases.json` (51 real cases).

- pattern_id: `e2-accum-outcome-switch-0001`  ·  pattern_type: `outcome_switch`
- Cases real, readings real; **support_count is the controlled variable** — like
  reading a thermometer placed in 0/50/100 °C water, not waiting for it to drift.

## Blind design (why this result is trustworthy)

A first pass leaked the tier number into the prompt (`support_count={tier}` plus
`~5/~20/~35/~50` anchors) and produced a prettier 0.60→0.85 curve — but part of
that slope came from the prompt feeding the number, not from the base rate. The
reported run is **blind**: the prompt is byte-identical across all four tiers and
reveals no count; the agent must read `support_count` from the pattern it
retrieves out of Elasticsearch. Verified end-to-end: in every tier the agent's
`severity_calibration.evidence[].support_count` equals the value actually injected
(5/20/35/50), and higher tiers score higher. This proves the lift comes from the
retrieved base rate, not the prompt — the strongest answer to "did you just write
the curve into the prompt?". Scope: E2 validates the **read** side of the loop
(retrieve base rate → calibrate); the write/accumulation side is E3.

## Reproduce

```bash
cd agents/
# per tier (5, 20, 35, 50):
uv run python scripts/e2_accumulation_curve.py --setup <N>
#   paste agents/evals/results/memory-loop-e2-2026-05-31/e2_prompt_support<N>.md
#   into drift_analyzer (uv run adk web / adk run); read calibrated_materiality
uv run python scripts/e2_accumulation_curve.py --record <N> <value>
uv run python scripts/e2_accumulation_curve.py --teardown
# after all four tiers:
uv run python scripts/e2_accumulation_curve.py --plot
```

Tagged `record_source=e2_accum_probe`; `--teardown` removes the injected pattern +
drift_events (the staged support is a controlled-experiment prop and must leave so
it never duplicates production patterns or corrupts the live base rate).

## v2 experiment suite (context)

- **E1** ✅ Flagship A/B: baseline 0.75 → treatment 1.0 (delta 0.25), negative 0.0.
  Read side, memory-vs-no-memory. (`memory-loop-ab-v2-2026-05-30/`)
- **E2** ✅ (this) Accumulation curve: base-rate **dose-response**, blind-verified.
- **E3** ✅ Curator correctness (`_e2e_probe.py`) + latency isolation
  (`_e3_latency_probe.py`, real-time retrieval p95 +2.3% under full curator load).
  Write/governance side.
- **E4** ✅ De-hardcoded retrieval generalizes off-fixture (economics case);
  agent does not misuse the clinical memory. (`e4_*`)

## Retrieval-stack fix (real gain beyond the demo)

Diagnosed and fixed the RRF "fake hybrid" (ELSER fused with itself, lexical leg
missing — a 2026-05-23 regression): added a `copy_to` text mirror
(`pattern_description_text`) and rebuilt in place so the BM25 leg is now genuinely
independent. All E2/E1/E4 retrieval rides this restored real hybrid path. See
contracts.md 2026-05-31 changelog + `project_hybrid_retrieval_fix`.
