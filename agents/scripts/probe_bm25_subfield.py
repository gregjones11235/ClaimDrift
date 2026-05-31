"""One-off: find a query form that gives REAL lexical (BM25) scoring on the
`pattern_description` semantic_text field, distinct from ELSER.

Why: diagnose_retrieval_stack.py showed the RRF "BM25 sub-retriever"
(`{"match": {"pattern_description": ...}}`) returns scores identical to the
`{"semantic": ...}` retriever — because a `match` on a semantic_text field is
auto-routed through ELSER. So the current RRF fuses ELSER with itself; the
lexical signal is missing. To turn the hybrid back on we need a query form that
hits a lexical (text) representation of the field.

This probe tries several candidate forms against the live read alias and prints
how many hits + the scores so we can tell SAME-as-ELSER from a genuine lexical
path from an EMPTY (subfield-absent) result.

READ-ONLY. Run from agents/:
    uv run python scripts/probe_bm25_subfield.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_AGENTS_ROOT / ".env")

from _shared.elastic_retrieval import _read_target  # noqa: E402
from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

QUERY = "diagnostic AI model quantitative performance metrics removed from abstract at publication"

CANDIDATES = {
    "match on field (current 'BM25' — expected == ELSER)": {
        "match": {"pattern_description": {"query": QUERY}}
    },
    "semantic (ELSER baseline)": {
        "semantic": {"field": "pattern_description", "query": QUERY}
    },
    "match on .text subfield": {
        "match": {"pattern_description.text": {"query": QUERY}}
    },
    "match on .inference.chunks.text": {
        "match": {"pattern_description.inference.chunks.text": {"query": QUERY}}
    },
}


def _result(client, target, body):
    try:
        resp = client.request(
            "POST", f"/{target}/_search",
            {"query": body, "size": 5, "_source": ["pattern_id", "pattern_type"]},
        )
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    hits = (resp.get("hits") or {}).get("hits") or []
    return [(h.get("_source", {}).get("pattern_id"), round(h.get("_score", 0.0), 4)) for h in hits], None


def main() -> None:
    client = ElasticsearchHttpClient()
    target = _read_target(client)
    print(f"read target: {target}\nquery: {QUERY}\n")

    # First: dump the actual field mapping so we KNOW what subfields exist.
    print("== actual mapping for pattern_description ==")
    try:
        mp = client.request("GET", f"/{target}/_mapping")
        for idx, body in mp.items():
            props = (body.get("mappings") or {}).get("properties") or {}
            pd = props.get("pattern_description")
            print(f"  index {idx}: pattern_description = {pd}")
    except Exception as exc:  # noqa: BLE001
        print(f"  mapping ERROR: {exc}")
    print()

    baseline = None
    for label, body in CANDIDATES.items():
        scores, err = _result(client, target, body)
        if err:
            print(f"-- {label}\n   ERROR: {err}\n")
            continue
        if "ELSER baseline" in label:
            baseline = scores
        tag = ""
        if not scores:
            tag = "  <== EMPTY (subfield absent / no lexical match)"
        elif baseline is not None and "ELSER baseline" not in label:
            tag = "  <== SAME AS ELSER" if scores == baseline else "  <== DIFFERENT (real lexical path!)"
        print(f"-- {label}  [{len(scores)} hits]{tag}")
        for pid, s in scores:
            print(f"   {s:>10}  {pid}")
        print()


if __name__ == "__main__":
    main()
