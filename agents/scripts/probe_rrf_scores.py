"""
Probe RRF score distribution on the live `drift_patterns` index.

Background — contracts.md §3.2.1 specifies "similarity_score >= 0.7" as
the cutoff for injecting patterns into Drift Analyzer. That number was
written when we were assuming cosine-style similarity. The 2026-05-22
infra-verification entry in the changelog notes that the actual scores
returned by `retriever.rrf` (BM25 + ELSER) are rank-based and live in
roughly 0.01–0.05, and tagged this as a TODO C to recalibrate.

This script does the recalibration. It runs a handful of queries with
known relevance against the real index and prints the score distribution
so we can pick a defensible cutoff to bake into agent defaults.

Run from the agents/ directory:

    uv run python scripts/probe_rrf_scores.py

Expects .env to be populated with ELASTIC_ENDPOINT and ELASTIC_API_KEY
(the same .env that adk web uses).

This script does NOT modify any document, index mapping, or contract.
It only reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `_shared` importable when running this script directly with
# `uv run python scripts/probe_rrf_scores.py`.
_AGENTS_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from _shared.elastic_retrieval import search_drift_patterns  # noqa: E402


# Each probe carries an expectation so the printout is interpretable
# without re-reading the demo seed. Demo seed currently has one pattern
# (pattern-demo-001, "COVID-related clinical preprints may show large
# effect-size reductions at publication with added hedging", tags:
# covid-19, clinical-trial, remote-monitoring).
PROBES: list[tuple[str, str, str]] = [
    (
        "highly_relevant_covid",
        "Hydroxychloroquine reduced viral load by 45% in early COVID-19 patients in this clinical trial.",
        "EXPECT: top-1 = pattern-demo-001, highest score in this run.",
    ),
    (
        "highly_relevant_general_effect_size",
        "Effect size for the intervention shrank substantially between preprint and published version.",
        "EXPECT: top-1 = pattern-demo-001 (matches 'effect-size reduction' phrasing).",
    ),
    (
        "medium_relevant_clinical_trial",
        "Randomized controlled trial of remote blood pressure monitoring after hospital discharge.",
        "EXPECT: pattern-demo-001 should still appear (domain overlap: clinical-trial / remote-monitoring) but at a lower score than the COVID probes.",
    ),
    (
        "medium_relevant_hedging",
        "The published version added hedging language and weakened the conclusion.",
        "EXPECT: pattern-demo-001 plausibly matches via 'added hedging'; score expected mid-range.",
    ),
    (
        "weakly_relevant_off_domain",
        "Transformer attention head pruning improves inference latency on long-context language models.",
        "EXPECT: pattern-demo-001 may still surface (only one doc in index, RRF always returns it) but with a notably lower score — this row sets the floor we want to be ABOVE.",
    ),
    (
        "weakly_relevant_off_domain_2",
        "Soil microbial community composition under different tillage regimes in temperate agriculture.",
        "EXPECT: similar to the previous off-domain probe — used to confirm the floor is stable across unrelated queries.",
    ),
]


def main() -> None:
    print("Probing /drift_patterns/_search with retriever.rrf …")
    print("(Each probe: top_k=5, min_score=None, exclude_demo_seed=False)")
    print()

    all_top_scores: list[float] = []
    floor_scores: list[float] = []

    for label, query, expectation in PROBES:
        print(f"── {label} ──")
        print(f"  query:       {query}")
        print(f"  expectation: {expectation}")
        try:
            results = search_drift_patterns(
                query_text=query,
                top_k=5,
                min_score=None,
                exclude_demo_seed=False,
            )
        except Exception as exc:  # noqa: BLE001 — surface anything so we can debug
            print(f"  ERROR: {exc}")
            print()
            continue

        if not results:
            print("  (no hits)")
            print()
            continue

        for rank, p in enumerate(results, start=1):
            description = (p.get("pattern_description") or "")[:80]
            print(
                f"  [{rank}] score={p['similarity_score']:.5f} "
                f"id={p.get('pattern_id')} "
                f"type={p.get('pattern_type')} "
                f"desc={description!r}"
            )

        top_score = results[0]["similarity_score"]
        all_top_scores.append(top_score)
        if label.startswith("weakly_relevant"):
            floor_scores.append(top_score)
        print()

    if not all_top_scores:
        print("No probes returned results — check ELASTIC_ENDPOINT / ELASTIC_API_KEY.")
        return

    print("── summary ──")
    print(f"  top-1 score across {len(all_top_scores)} probes: "
          f"min={min(all_top_scores):.5f}  max={max(all_top_scores):.5f}")
    if floor_scores:
        floor_max = max(floor_scores)
        print(f"  off-domain (floor) top-1: max={floor_max:.5f}")
        print()
        print("  Suggested threshold candidates:")
        print(f"    - strict (exclude off-domain):     min_score > {floor_max:.5f}")
        print(f"    - lenient (keep medium-relevant):  min_score ~ {floor_max * 0.6:.5f}")
        print()
        print("  Next step: pick a value, propose updating contracts.md §3.2 / §3.5")
        print("  retrieval rules. Do NOT bake the value into search_drift_patterns()")
        print("  defaults yet — let drift_analyzer / memory_synthesizer pass it"
              " explicitly so we keep the choice visible in the agent code.")


if __name__ == "__main__":
    main()
