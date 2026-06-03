"""E3 latency-isolation probe — proves the dedicated-endpoint claim of B.2.

The companion `_e2e_probe.py` proves curator *correctness* (merge / evict /
real-LLM judgment). This script proves the *other* half of E3
(design.md Part E / E3, contracts.md §3.6 + §2.2.5): that while the curator is
running, real-time `search_drift_patterns` latency is NOT degraded — i.e. the
dedicated `claimdrift-elser-batch` endpoint + keyword pre-filter + dry-run
isolation actually shield the live read path from curator load.

Method (deterministic, read-only against real rows):
  1. Inject the same probe rows as _e2e_probe (record_source="curator_e2e_probe")
     so the curator has real duplicates/garbage to chew on — gives it ELSER
     recall + a real Gemini judgment to do, i.e. representative load.
  2. BASELINE: run a fixed battery of `search_drift_patterns` queries serially,
     curator idle. Record per-query wall-clock; compute p50 / p95.
  3. UNDER-LOAD: start the curator (dry-run) on a background thread; while it
     runs, run the SAME battery again. Record p50 / p95.
  4. ASSERT: under-load p95 must not exceed baseline p95 by more than
     --max-regression-pct (default 50%). A pass demonstrates live retrieval is
     isolated from curator load at demo / low-QPS scale.

Safety: curator runs DRY-RUN (writes nothing to real rows). Probe rows are
injected then deleted. Read path is real `search_drift_patterns`.

Run from agents/:
    uv run python -m pattern_curator.scripts._e3_latency_probe
    uv run python -m pattern_curator.scripts._e3_latency_probe --rounds 30
    uv run python -m pattern_curator.scripts._e3_latency_probe --max-regression-pct 75
"""
from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (_AGENTS_ROOT, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_AGENTS_ROOT / ".env")

from _shared.elastic_retrieval import search_drift_patterns  # noqa: E402
from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402
from pattern_curator.curator import run_curator  # noqa: E402
from pattern_curator.es_ops import PatternStore  # noqa: E402
from pattern_curator.scripts._e2e_probe import _cleanup, _inject  # noqa: E402

# A small battery of representative live queries. Deliberately varied so the
# measurement is not a single cached query path. These are the same shapes a
# real Drift Analyzer / Memory Synthesizer retrieval would issue.
QUERIES = [
    "Primary efficacy endpoint demoted to exploratory secondary outcome in a clinical trial at publication.",
    "COVID-19 randomized controlled trial effect size reduced substantially between preprint and publication.",
    "Diagnostic AI model performance metrics removed from the abstract at publication.",
    "Published version added hedging language and weakened the headline conclusion.",
    "Agricultural field-trial yield claim narrowed to specific cultivars after peer review.",
]


def _battery(rounds: int) -> list[float]:
    """Run the query battery `rounds` times serially; return per-query
    latencies in milliseconds."""
    latencies: list[float] = []
    for _ in range(rounds):
        for q in QUERIES:
            t0 = time.perf_counter()
            try:
                search_drift_patterns(query_text=q, top_k=5, min_score=None,
                                      exclude_demo_seed=False)
            except Exception as exc:  # noqa: BLE001
                print(f"  WARN: query failed ({exc}); recording as outlier")
                latencies.append(float("inf"))
                continue
            latencies.append((time.perf_counter() - t0) * 1000.0)
    return latencies


def _pctl(values: list[float], pct: float) -> float:
    finite = sorted(v for v in values if v != float("inf"))
    if not finite:
        return float("inf")
    k = max(0, min(len(finite) - 1, round((pct / 100.0) * (len(finite) - 1))))
    return finite[k]


def _summary(label: str, lat: list[float]) -> dict[str, float]:
    p50, p95 = _pctl(lat, 50), _pctl(lat, 95)
    finite = [v for v in lat if v != float("inf")]
    mean = statistics.fmean(finite) if finite else float("inf")
    print(f"[{label}] n={len(lat)}  mean={mean:.1f}ms  p50={p50:.1f}ms  p95={p95:.1f}ms")
    return {"p50": p50, "p95": p95, "mean": mean}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=20,
                    help="Query-battery repetitions per phase (default 20 -> 100 queries).")
    ap.add_argument("--max-regression-pct", type=float, default=50.0,
                    help="Max allowed p95 regression under load before FAIL (default 50%%).")
    ap.add_argument("--keep", action="store_true", help="Leave probe rows after run.")
    args = ap.parse_args()

    client = ElasticsearchHttpClient()
    _cleanup(client)
    ids = _inject(client)
    print("injected probe rows for curator load:", list(ids.values()))
    store = PatternStore(client=client)

    try:
        # 1. BASELINE — curator idle.
        print("\n== BASELINE (curator idle) ==")
        base = _summary("baseline", _battery(args.rounds))

        # 2. UNDER LOAD — curator runs dry-run on a background thread.
        print("\n== UNDER LOAD (curator dry-run running) ==")
        curator_done = threading.Event()
        curator_err: list[BaseException] = []

        def _spin_curator() -> None:
            try:
                # Loop the dry-run curator so it stays busy for the whole
                # measurement window, exercising scan + ELSER recall + judgment.
                while not curator_done.is_set():
                    run_curator(store, apply=False, since=None,
                                referential_check=True, max_judgments=50,
                                log=lambda _m: None)
            except BaseException as exc:  # noqa: BLE001
                curator_err.append(exc)

        t = threading.Thread(target=_spin_curator, daemon=True)
        t.start()
        try:
            load = _summary("under-load", _battery(args.rounds))
        finally:
            curator_done.set()
            t.join(timeout=120)

        if curator_err:
            print(f"  NOTE: curator thread raised {curator_err[0]!r} "
                  "(load may have been lighter than intended)")

        # 3. ASSERT — p95 must not regress beyond the threshold.
        print("\n== VERDICT ==")
        if base["p95"] in (0.0, float("inf")):
            print("FAIL: baseline p95 unusable; cannot assess regression")
            raise SystemExit(1)
        regression = (load["p95"] - base["p95"]) / base["p95"] * 100.0
        print(f"  baseline p95 = {base['p95']:.1f}ms")
        print(f"  under-load p95 = {load['p95']:.1f}ms")
        print(f"  p95 regression = {regression:+.1f}%  (allowed <= {args.max_regression_pct:.0f}%)")
        if regression <= args.max_regression_pct:
            print("PASS: live retrieval latency is isolated from curator load (E3 / B.2).")
        else:
            print("FAIL: curator load degraded live retrieval beyond the allowed margin.")
            raise SystemExit(1)
    finally:
        if not args.keep:
            n = _cleanup(client)
            print(f"\ncleaned up {n} probe row(s).")
        else:
            print("\n--keep set; probe rows left in the cluster.")


if __name__ == "__main__":
    main()
