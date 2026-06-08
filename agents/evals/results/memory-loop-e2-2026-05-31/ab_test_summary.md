# Memory Loop — Severity-Calibration A/B Test (production prompt)

**What this demonstrates:** an A/B test of the memory loop's read side. The SAME
real, deliberately-ambiguous drift case is analyzed by the SAME shipped
`drift_analyzer` prompt under three memory conditions. Memory does not change
*whether* the agent flags the drift — it changes the **reasoning** behind the
judgment: from "this change is intrinsically significant" to "this is a known,
recurring manipulation pattern." The score rises once (no-memory → memory) and
then plateaus, which is the honest result: there is a sensible severity ceiling,
and the agent does not inflate the number just because more evidence accrues.

Captured 2026-06-08 against the production `drift_analyzer` INSTRUCTION (the
same prompt that ships — see `agents/drift_analyzer/agent.py`). The agent's user
message is ONLY the case JSON, byte-identical to what the production supervisor
sends; the entire method lives in the system prompt. Raw outputs:
`ab_test_today.json`.

## The case (fixed across all conditions — REAL)

Tasimelteon SET trial for Non-24-Hour Sleep–Wake Disorder (Lockley et al.,
Lancet 2015;386:1754-64; ClinicalTrials.gov **NCT01163032**), flagged by the
Oxford CEBM **COMPARE** project as `primary_switched=1`.

- **Pre-registered:** TWO co-primary endpoints — (1) aMT6s entrainment, (2) a
  clinical response (entrainment + N24CRS ≥3).
- **Published:** endpoint (1) reported as THE primary; endpoint (2) demoted to a
  subordinate "planned step-down primary endpoint".
- **Why deliberately ambiguous:** a step-down / hierarchical testing order is a
  legitimate pre-specified design, so from the diff alone the severity is
  genuinely defensible-or-serious. This is exactly where a historical base rate
  should change the judgment.

## Result — three memory conditions, same case, same prompt

| condition | retrieved_patterns_used | materiality | what the rationale says |
|---|---|---|---|
| **no memory** (cold start) | `[]` | **0.75** | "**No relevant memory was retrieved.** Score based on the **intrinsic significance** of downgrading a co-primary endpoint… *can be* a vehicle for selective reporting." |
| **memory, support=5** | `e2-accum-outcome-switch-0001` | **0.85** | "Retrieved memory confirms this is a **known, recurring pattern**… often used to **inflate apparent success**. Support count (5) **increases confidence** this drift is not incidental." |
| **memory, support=20** | `e2-accum-outcome-switch-0001` | **0.85** | "The retrieved pattern, which has **strong support (support_count=20)**, confirms this is a **recurring and high-severity issue**… the drift is an instance of a known problematic pattern." |

### Read the rationale, not just the number

The number moves once (0.75 → 0.85) and then holds. The *reasoning* is where the
memory loop shows its value, and it shifts at every step:

1. **No memory:** the agent can only argue from the case in isolation — "this
   *can be* a vehicle for selective reporting." A hedge, grounded in nothing but
   the single diff.
2. **support=5:** the agent now **recognizes the phenomenon** — "a known,
   recurring pattern often used to inflate apparent success." It has stopped
   guessing and started identifying.
3. **support=20:** same recognition, but **more assertive** — "strong support…
   an instance of a known problematic pattern." The hedging is gone.

So the agent goes from *judging an isolated event* to *recognizing a known
manipulation tactic*, and grows more confident as the base rate strengthens.
The flat 0.85 is a feature, not a flaw: it shows the system has a principled
ceiling and refuses to keep inflating severity just because evidence piles up.

> Honest scope note: the lift is memory-vs-no-memory (0.75 → 0.85). Higher
> support counts sharpen the *rationale* (weak → strong base rate) without
> pushing the score past its ceiling. We deliberately do NOT claim a
> monotonically climbing number across support tiers — the production prompt
> does not produce one on this case, and the rationale evolution is the truer
> signal.

## How memory was injected / removed (controlled experiment)

- The retrieved pattern is a controlled probe: N genuine, registered,
  DOI-bearing outcome-switch cases audited by COMPARE, tagged
  `record_source=e2_accum_probe`, with `support_count == len(source_event_ids)`.
- Inject one tier: `e2_accumulation_curve.py --setup <N>`.
- No-memory control: `e2_accumulation_curve.py --baseline` (clears the probe so
  retrieval comes up empty; same case payload).
- Always remove the probe afterward: `--teardown` (the staged support is a
  controlled-experiment prop and must leave so it never duplicates a production
  pattern or corrupts the live base rate).

## Reproduce

```bash
cd agents/
# no-memory baseline:
uv run python scripts/e2_accumulation_curve.py --baseline
#   paste evals/results/memory-loop-e2-2026-05-31/e2_prompt_baseline.md into the
#   production drift_analyzer (uv run adk web); confirm retrieved_patterns_used == []
# memory conditions (5, then 20):
uv run python scripts/e2_accumulation_curve.py --setup 5
#   paste e2_prompt_support5.md; read severity_calibration
uv run python scripts/e2_accumulation_curve.py --teardown
uv run python scripts/e2_accumulation_curve.py --setup 20
#   paste e2_prompt_support20.md; read severity_calibration
uv run python scripts/e2_accumulation_curve.py --teardown
```

The agent reads `support_count` from the pattern it retrieves out of
Elasticsearch — the case payload never names a count — so any rationale shift
comes from the retrieved base rate, not from the prompt.
