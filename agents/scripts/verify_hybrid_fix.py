"""Verify the hybrid-retrieval fix actually restored an independent lexical leg.

Run this AFTER `manage_pattern_alias.py rebuild --apply` has swapped the read
alias onto a freshly-built index whose mapping has
`pattern_description` copy_to `pattern_description_text`.

Three checks, all read-only against the live read alias:

  1. LEXICAL FIELD POPULATED: the copy_to mirror `pattern_description_text` is
     non-empty and lexically searchable on the rebuilt index (proves copy_to ran
     during reindex).
  2. BM25 != ELSER: the two RRF sub-retrievers now return DIFFERENT raw-score
     orderings for a lexically-distinctive query (proves the fusion is no longer
     ELSER-with-itself). Before the fix these were byte-identical.
  3. RRF still healthy: the fused query returns results (sanity).

Run from agents/:
    uv run python scripts/verify_hybrid_fix.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_AGENTS_ROOT, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_AGENTS_ROOT / ".env")

from _shared.elastic_retrieval import _read_target, search_drift_patterns  # noqa: E402
from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

# A query with distinctive lexical tokens so BM25 and ELSER are likely to
# disagree on ordering (proper nouns / rare terms are where lexical adds value).
QUERY = "primary endpoint switched to medication adherence surrogate in cardiology RCT"


def _order(client, target, body):
    resp = client.request(
        "POST", f"/{target}/_search",
        {"query": body, "size": 5, "_source": ["pattern_id"]},
    )
    hits = (resp.get("hits") or {}).get("hits") or []
    return [(h.get("_source", {}).get("pattern_id"), round(h.get("_score", 0.0), 4)) for h in hits]


def main() -> int:
    client = ElasticsearchHttpClient()
    target = _read_target(client)
    print(f"read target: {target}\nquery: {QUERY}\n")
    failures = []

    # 1. lexical field populated
    resp = client.request(
        "POST", f"/{target}/_search",
        {"query": {"exists": {"field": "pattern_description_text"}}, "size": 0},
    )
    n_text = (resp.get("hits") or {}).get("total", {}).get("value", 0)
    total = client.request("POST", f"/{target}/_count", {}).get("count", 0)
    print(f"[1] docs with non-empty pattern_description_text: {n_text}/{total}")
    if n_text == 0:
        print("    FAIL: lexical mirror is empty — copy_to did not run. Did you rebuild "
              "onto the new mapping and swap the alias?")
        failures.append("lexical field empty")
    elif n_text < total:
        print(f"    WARN: only {n_text}/{total} populated — partial rebuild?")
    else:
        print("    PASS: every doc has the lexical mirror populated.")

    # 2. BM25 (lexical) vs ELSER (semantic) must now differ
    bm25 = _order(client, target, {"match": {"pattern_description_text": {"query": QUERY}}})
    elser = _order(client, target, {"semantic": {"field": "pattern_description", "query": QUERY}})
    print(f"\n[2] BM25 (lexical) top-5: {[p for p, _ in bm25]}")
    print(f"    ELSER (semantic) top-5: {[p for p, _ in elser]}")
    if not bm25:
        print("    FAIL: BM25 lexical query returned nothing.")
        failures.append("bm25 empty")
    elif bm25 == elser:
        print("    FAIL: BM25 == ELSER ordering — still fused with itself.")
        failures.append("bm25 == elser")
    else:
        print("    PASS: lexical and semantic legs now rank differently (true hybrid).")

    # 3. RRF sanity
    fused = search_drift_patterns(query_text=QUERY, top_k=5, min_score=None, exclude_demo_seed=False)
    print(f"\n[3] RRF fused top-5: {[p.get('pattern_id') for p in fused]}")
    if not fused:
        print("    FAIL: fused retrieval returned nothing.")
        failures.append("rrf empty")
    else:
        print("    PASS: fused retrieval healthy.")

    print()
    if failures:
        print(f"VERDICT: FAIL ({failures})")
        return 1
    print("VERDICT: PASS — hybrid retrieval restored (lexical + semantic genuinely fused).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
