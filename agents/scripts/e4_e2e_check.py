"""E4 (layer 2) checker — score the drift_analyzer output captured from the
economics effect-size-reduction case set up by e4_e2e_setup.py.

Asserts the de-hardcoded, structured-descriptor retrieval generalized end-to-end:
  1. The economics pattern was retrieved + used.
  2. The clinical outcome-switch memory was NOT misused.
  3. The output reasons about economics / effect-size, not AI-diagnostic claims
     (the old hard-coded hint's domain).
  4. No invented machine fields (event_id / analyzed_at null), no nested
     materiality_score.

Run from agents/ after saving the agent output as e4_treatment.json:
    uv run python scripts/e4_e2e_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = _AGENTS_ROOT / "evals" / "results" / "memory-loop-e4-2026-05-31"
ECON_PATTERN_ID = "e4-e2e-econ-effectsize-0001"
CLINICAL_PATTERN_ID = "memory-loop-v2-outcome-switch-0101"


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _pattern_ids(out):
    ids = set(str(x) for x in _as_list(out.get("retrieved_patterns_used")) if x)
    cal = out.get("severity_calibration")
    if isinstance(cal, dict):
        ids.update(str(x) for x in _as_list(cal.get("memory_pattern_ids")) if x)
    return ids


def _text(out):
    pieces = []
    for k in ("drift_summary", "memory_usage_rationale"):
        if isinstance(out.get(k), str):
            pieces.append(out[k])
    cal = out.get("severity_calibration")
    if isinstance(cal, dict) and isinstance(cal.get("rationale"), str):
        pieces.append(cal["rationale"])
    for d in _as_list(out.get("claim_diffs")):
        if isinstance(d, dict) and isinstance(d.get("change_description"), str):
            pieces.append(d["change_description"])
    return "\n".join(pieces).lower()


def _check(cond, label, failures):
    print(f"{'PASS' if cond else 'FAIL'}: {label}")
    if not cond:
        failures.append(label)


def main() -> int:
    path = RUN_DIR / "e4_treatment.json"
    if not path.exists():
        raise SystemExit(f"missing {path} — run e4_e2e_setup.py --setup, then the agent, save output here.")
    out = json.loads(path.read_text())
    failures = []

    used = _pattern_ids(out)
    print(f"retrieved_patterns_used + memory_pattern_ids = {sorted(used)}\n")

    _check(ECON_PATTERN_ID in used, f"economics pattern {ECON_PATTERN_ID} retrieved/used", failures)
    _check(CLINICAL_PATTERN_ID not in used,
           f"clinical outcome-switch memory {CLINICAL_PATTERN_ID} NOT misused", failures)

    txt = _text(out)
    _check(any(w in txt for w in ("economic", "effect size", "effect-size", "coefficient",
                                  "significance", "minimum wage", "employment")),
           "output reasons about economics / effect-size", failures)
    _check("diagnostic" not in txt and "auroc" not in txt,
           "output is NOT dominated by AI-diagnostic (old hard-coded hint domain)", failures)

    _check(out.get("event_id") is None and out.get("analyzed_at") is None,
           "no invented event_id / analyzed_at", failures)
    nested = any(isinstance(d, dict) and "materiality_score" in d for d in _as_list(out.get("claim_diffs")))
    _check(not nested, "no nested materiality_score in claim_diffs", failures)

    score = out.get("materiality_score")
    _check(isinstance(score, (int, float)) and 0 <= float(score) <= 1,
           "materiality_score numeric in [0,1]", failures)

    print()
    if failures:
        print(f"E4 layer-2 VERDICT: FAIL ({len(failures)} failed)")
        return 1
    print("E4 layer-2 VERDICT: PASS — drift_analyzer generalized end-to-end to an "
          "off-fixture economics case with de-hardcoded structured retrieval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
