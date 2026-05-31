"""One-off diagnostic: is the retrieval stack actually discriminating, or is
the narrow RRF score band a sign the top-k / ELSER wiring is broken?

Motivation: `probe_rrf_scores.py` shows every top-1 RRF score clustered around
0.0286-0.0328. That looks alarming ("no discrimination"), but RRF scores are
rank-based by construction: top-1 ceiling is ~2 * 1/(rank_constant+1) ~= 0.0328
for two retrievers at rank_constant=60, and consecutive ranks differ by ~0.0003.
So the SCORE band being narrow proves nothing about discrimination — the RANKING
does. This script separates the two retrievers and inspects raw scores + ranks
so we can confirm the stack is healthy (and that the real gap is simply "no
strong outcome_switch pattern exists in the index yet").

It issues three queries per probe against the live read alias:
  1. BM25-only   (lexical; real _score, NOT rank-fused) -> should rank by term overlap
  2. ELSER-only  (semantic; real _score)                -> should rank by meaning
  3. RRF fused   (what search_drift_patterns actually returns)

READ-ONLY. Writes nothing. Run from agents/:
    uv run python scripts/diagnose_retrieval_stack.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_AGENTS_ROOT / ".env")

from _shared.elastic_retrieval import (  # noqa: E402
    _read_target,
    search_drift_patterns,
)
from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

# Field bound to the ELSER endpoint (semantic_text).
SEMANTIC_FIELD = "pattern_description"

PROBES = [
    ("outcome_switch (clinical)",
     "clinical trial primary efficacy endpoint demoted to exploratory secondary outcome at publication"),
    ("diagnostic AI claim_disappearance",
     "diagnostic AI model quantitative performance metrics removed from abstract at publication"),
    ("effect size reduction (econ)",
     "empirical economics causal effect magnitude revised downward after robustness checks"),
]

_SOURCE = ["pattern_id", "pattern_type", "domain_tags", "support_count"]


def _bm25(client, target, query, k=5):
    # Query the analyzed `text` copy_to mirror, NOT the semantic_text field
    # itself — a `match` on `pattern_description` auto-routes through ELSER and
    # would make this leg identical to _elser (the very bug we fixed). The
    # mirror is populated at index time by copy_to (see drift_patterns_v2.json).
    body = {
        "query": {"match": {f"{SEMANTIC_FIELD}_text": query}},
        "size": k,
        "_source": _SOURCE,
    }
    return client.request("POST", f"/{target}/_search", body)


def _elser(client, target, query, k=5):
    # semantic_text field -> the `semantic` query runs ELSER inference on the
    # query text and scores against the stored sparse vectors. Real _score.
    body = {
        "query": {"semantic": {"field": SEMANTIC_FIELD, "query": query}},
        "size": k,
        "_source": _SOURCE,
    }
    return client.request("POST", f"/{target}/_search", body)


def _print_hits(label, response):
    hits = (response.get("hits") or {}).get("hits") or []
    print(f"  [{label}] {len(hits)} hits")
    if not hits:
        print("    (none)")
        return
    raw_scores = [h.get("_score", 0.0) for h in hits]
    for rank, h in enumerate(hits, 1):
        s = h.get("_source", {}) or {}
        print(f"    {rank}. score={h.get('_score', 0.0):.4f} "
              f"{s.get('pattern_id')} type={s.get('pattern_type')} support={s.get('support_count')}")
    spread = max(raw_scores) - min(raw_scores) if raw_scores else 0.0
    print(f"    raw-score spread top..bottom = {spread:.4f} "
          f"({'DISCRIMINATING' if spread > 0.5 else 'flat — inspect'})")


def main() -> None:
    client = ElasticsearchHttpClient()
    target = _read_target(client)
    print(f"read target (alias/index): {target}\n")

    for name, query in PROBES:
        print(f"== probe: {name} ==")
        print(f"   query: {query}")
        try:
            _print_hits("BM25-only (lexical, real _score)", _bm25(client, target, query))
        except Exception as exc:  # noqa: BLE001
            print(f"  BM25 ERROR: {exc}")
        try:
            _print_hits("ELSER-only (semantic, real _score)", _elser(client, target, query))
        except Exception as exc:  # noqa: BLE001
            print(f"  ELSER ERROR: {exc}")
        fused = search_drift_patterns(query_text=query, top_k=5, min_score=None,
                                      exclude_demo_seed=False)
        print(f"  [RRF fused (rank-based, what the agent sees)] {len(fused)} hits")
        for rank, p in enumerate(fused, 1):
            print(f"    {rank}. rrf={p['similarity_score']:.4f} {p.get('pattern_id')} "
                  f"type={p.get('pattern_type')} support={p.get('support_count')}")
        print()

    print("How to read this:")
    print("  - BM25 / ELSER raw _scores SHOULD have a wide spread (>0.5) and order")
    print("    by relevance. That proves the retrievers discriminate.")
    print("  - The RRF column is SUPPOSED to be a narrow 0.03 band — that is the")
    print("    rank-fusion scale, not a defect. Judge it by ORDER, not by score gap.")
    print("  - If NO probe surfaces a pattern_type=outcome_switch row, the index")
    print("    simply lacks that memory yet — that is the gap E2 fills by injection,")
    print("    not a retrieval bug.")


if __name__ == "__main__":
    main()
