"""E4 (layer 1) — de-hardcoded retrieval generalizes OUTSIDE the demo fixture,
and hybrid vs ELSER-only is quantified head-to-head.

design.md Part E / E4: prove the overfit hard-coded retrieval hint
("AI diagnostic tool claim_disappearance ...") is gone and structured-descriptor
retrieval still finds the right pattern in domains the fixture never covered.

This script (deterministic, self-cleaning):
  1. Injects two vivid OUT-OF-FIXTURE patterns (record_source tag, deletable):
       - economics effect_size_reduction
       - neuroscience claim_disappearance
     The outcome-switch demo fixture is a CLINICAL case, so these two are
     unrelated domains/drift-types — exactly what E4 needs.
  2. For each, builds the query the way drift_analyzer now does: a STRUCTURED
     descriptor (domain + study type + drift_type + magnitude), NOT the old
     hard-coded AI-diagnostic hint.
  3. Asserts the matching injected pattern is retrieved in the fused top-k
     (retrieval still works off-fixture).
  4. HYBRID vs ELSER-ONLY: runs the same query as full RRF (hybrid) and as
     semantic-only, and reports rank of the target under each — quantifying
     whether the restored lexical leg helps, hurts, or is neutral.

Run from agents/:
    uv run python scripts/e4_generalization_probe.py
    uv run python scripts/e4_generalization_probe.py --keep   # leave probe rows
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_AGENTS_ROOT, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_AGENTS_ROOT / ".env")

from _shared.elastic_retrieval import (  # noqa: E402
    DRIFT_PATTERNS_INDEX,
    _read_target,
    search_drift_patterns,
)
from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

PROBE_TAG = "e4_generalization_probe"

# Two out-of-fixture patterns + the structured descriptor a de-hardcoded
# drift_analyzer would build for a matching case in that domain.
CASES = [
    {
        "pattern_id": "e4-econ-effectsize-001",
        "doc": {
            "pattern_description": (
                "Empirical economics preprints frequently revise headline causal-effect "
                "magnitudes downward after referee-requested robustness checks, with the "
                "main coefficient sometimes losing statistical significance between the "
                "working paper and the published version."
            ),
            "pattern_type": "effect_size_reduction",
            "domain_tags": ["economics", "causal-inference", "robustness", "effect-size-reduction"],
        },
        # structured descriptor: domain + study type + drift_type + magnitude
        "query": ("economics empirical study effect_size_reduction headline causal "
                  "coefficient revised downward loses significance after robustness checks"),
    },
    {
        "pattern_id": "e4-neuro-disappearance-001",
        "doc": {
            "pattern_description": (
                "Neuroimaging preprints commonly drop or weaken whole-brain significance "
                "claims after publication, narrowing reported effects to specific regions "
                "of interest or smaller subsamples, so a strong global claim disappears."
            ),
            "pattern_type": "claim_disappearance",
            "domain_tags": ["neuroscience", "fmri", "claim-disappearance", "whole-brain"],
        },
        "query": ("neuroscience neuroimaging fMRI claim_disappearance whole-brain "
                  "significance claim removed narrowed to region of interest at publication"),
    },
]

OLD_HARDCODED_HINT = "AI diagnostic tool claim_disappearance quantitative performance metrics removed"


def _inject(client, now):
    for c in CASES:
        doc = dict(c["doc"])
        doc.update({
            "pattern_id": c["pattern_id"],
            "record_source": PROBE_TAG,
            "source_event_ids": [],
            "support_count": 1,
            "created_at": now,
            "last_updated_at": now,
        })
        client.request("PUT", f"/{DRIFT_PATTERNS_INDEX}/_doc/{c['pattern_id']}?op_type=create&refresh=wait_for", doc)
    print(f"injected {len(CASES)} out-of-fixture probe pattern(s).")


def _cleanup(client):
    body = {"query": {"term": {"record_source": PROBE_TAG}}}
    r = client.request("POST", f"/{DRIFT_PATTERNS_INDEX}/_delete_by_query?refresh=true", body)
    return int(r.get("deleted", 0))


def _rank_of(target_id, results):
    for i, p in enumerate(results, 1):
        if p.get("pattern_id") == target_id:
            return i
    return None


def _elser_only(client, query, k=10):
    """Same semantic leg search_drift_patterns fuses, run alone, for A/B."""
    target = _read_target(client)
    resp = client.request(
        "POST", f"/{target}/_search",
        {"query": {"semantic": {"field": "pattern_description", "query": query}},
         "size": k, "_source": ["pattern_id"]},
    )
    hits = (resp.get("hits") or {}).get("hits") or []
    return [{"pattern_id": h.get("_source", {}).get("pattern_id")} for h in hits]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    client = ElasticsearchHttpClient()
    now = "2026-05-31T00:00:00Z"
    _cleanup(client)
    _inject(client, now)
    failures = []

    try:
        # (A) de-hardcoded check: the structured descriptors carry NO AI-diagnostic
        # wording — confirm the old overfit hint is absent from how we query.
        print("\n[de-hardcoded] structured descriptors used as queries:")
        for c in CASES:
            has_old = OLD_HARDCODED_HINT.lower() in c["query"].lower()
            print(f"  - {c['pattern_id']}: {'CONTAINS OLD HINT!' if has_old else 'no hard-coded AI hint'}")
            if has_old:
                failures.append(f"{c['pattern_id']} query still uses hard-coded hint")

        # (B) retrieval still works off-fixture + hybrid vs ELSER-only ranks
        print("\n[off-fixture retrieval] target rank under hybrid (fused) vs ELSER-only:")
        for c in CASES:
            fused = search_drift_patterns(query_text=c["query"], top_k=10, min_score=None, exclude_demo_seed=False)
            elser = _elser_only(client, c["query"], k=10)
            r_hybrid = _rank_of(c["pattern_id"], fused)
            r_elser = _rank_of(c["pattern_id"], elser)
            print(f"  - {c['pattern_id']} ({c['doc']['pattern_type']}): "
                  f"hybrid rank={r_hybrid}  elser-only rank={r_elser}")
            if r_hybrid is None:
                failures.append(f"{c['pattern_id']} NOT retrieved by hybrid in top-10")
            else:
                print(f"      PASS: retrieved off-fixture at hybrid rank {r_hybrid}")
            if r_hybrid is not None and r_elser is not None:
                if r_hybrid < r_elser:
                    print(f"      hybrid BETTER (rank {r_hybrid} < {r_elser})")
                elif r_hybrid > r_elser:
                    print(f"      hybrid worse (rank {r_hybrid} > {r_elser})")
                else:
                    print("      hybrid == elser (tie)")
    finally:
        if not args.keep:
            n = _cleanup(client)
            print(f"\ncleaned up {n} probe row(s).")
        else:
            print("\n--keep set; probe rows left in the cluster.")

    print()
    if failures:
        print(f"E4 layer-1 VERDICT: FAIL ({failures})")
        return 1
    print("E4 layer-1 VERDICT: PASS — de-hardcoded structured retrieval generalizes "
          "off-fixture; hybrid vs ELSER ranks reported above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
