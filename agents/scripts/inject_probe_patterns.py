"""
Inject synthetic drift_patterns to make RRF score probing meaningful.

Why: the live `drift_patterns` index currently holds 1 doc
(`pattern-demo-001`), so `retriever.rrf` returns the same constant score
(~0.03279) for every query — no distribution to calibrate against. This
script writes 5 cross-domain `drift_pattern` rows, each tagged with
`record_source = "synthetic_probe"` (NOT `demo_seed` — Jeremy's marker
is reserved for the curated demo case; we use a different value so the
two sets are filterable independently).

After running probe_rrf_scores.py with these in the index, run
cleanup_probe_patterns.py to delete them — they are NOT meant to ship.

Run from agents/:
    uv run python scripts/inject_probe_patterns.py

This script DOES write to the live cluster. It is the only file in
agents/scripts/ that mutates the index.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

# Re-uses the same path setup as elastic_retrieval — adds repo root to
# sys.path so `ingestion.common.elastic` is importable.
from _shared.elastic_retrieval import (  # noqa: E402
    DRIFT_PATTERNS_INDEX,
)
from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

PROBE_RECORD_SOURCE = "synthetic_probe"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_probe_patterns(ts: str) -> list[dict]:
    """Five patterns spanning distinct domains so RRF has real rank
    spread to fuse. Each row carries `record_source = "synthetic_probe"`
    so cleanup_probe_patterns.py can delete the whole set in one call.
    """
    return [
        {
            "pattern_id": "probe-ml-001",
            "pattern_description": (
                "Machine learning preprints sometimes report inflated benchmark gains "
                "that shrink after independent reproduction, with the published version "
                "often softening claims about generality."
            ),
            "pattern_type": "effect_size_reduction",
            "domain_tags": ["machine-learning", "benchmark", "reproducibility"],
            "source_event_ids": [],
            "support_count": 1,
            "record_source": PROBE_RECORD_SOURCE,
            "created_at": ts,
            "last_updated_at": ts,
        },
        {
            "pattern_id": "probe-agri-001",
            "pattern_description": (
                "Agricultural field trials often see yield-improvement claims narrowed "
                "to specific cultivars or soil conditions between preprint and final "
                "publication, frequently via added scope qualifiers."
            ),
            "pattern_type": "hedging_addition",
            "domain_tags": ["agriculture", "field-trial", "yield"],
            "source_event_ids": [],
            "support_count": 1,
            "record_source": PROBE_RECORD_SOURCE,
            "created_at": ts,
            "last_updated_at": ts,
        },
        {
            "pattern_id": "probe-econ-001",
            "pattern_description": (
                "Empirical economics preprints frequently revise causal-effect "
                "magnitudes downward after referee-requested robustness checks, with "
                "the headline coefficient sometimes losing statistical significance."
            ),
            "pattern_type": "effect_size_reduction",
            "domain_tags": ["economics", "causal-inference", "robustness"],
            "source_event_ids": [],
            "support_count": 1,
            "record_source": PROBE_RECORD_SOURCE,
            "created_at": ts,
            "last_updated_at": ts,
        },
        {
            "pattern_id": "probe-neuro-001",
            "pattern_description": (
                "Neuroimaging preprints commonly drop or weaken whole-brain "
                "significance claims after publication, narrowing reported effects to "
                "specific regions of interest or smaller subsamples."
            ),
            "pattern_type": "claim_disappearance",
            "domain_tags": ["neuroscience", "fmri", "multiple-comparisons"],
            "source_event_ids": [],
            "support_count": 1,
            "record_source": PROBE_RECORD_SOURCE,
            "created_at": ts,
            "last_updated_at": ts,
        },
        {
            "pattern_id": "probe-psych-001",
            "pattern_description": (
                "Social psychology preprints sometimes have headline behavioral "
                "effects fully removed by the published version after preregistered "
                "replication failures, leaving only exploratory secondary analyses."
            ),
            "pattern_type": "claim_disappearance",
            "domain_tags": ["psychology", "replication", "preregistration"],
            "source_event_ids": [],
            "support_count": 1,
            "record_source": PROBE_RECORD_SOURCE,
            "created_at": ts,
            "last_updated_at": ts,
        },
    ]


def main() -> None:
    ts = _now()
    patterns = _build_probe_patterns(ts)
    client = ElasticsearchHttpClient()

    rows = [(p["pattern_id"], p) for p in patterns]
    response = client.bulk_index(DRIFT_PATTERNS_INDEX, rows)

    if response.get("errors"):
        print("Bulk index reported errors:")
        for item in response.get("items", []):
            index_result = item.get("index", {})
            if index_result.get("error"):
                print(f"  {index_result.get('_id')}: {index_result['error']}")
        raise SystemExit(1)

    count = response.get("count", len(rows))
    took = response.get("took")
    print(f"Injected {count} probe patterns into '{DRIFT_PATTERNS_INDEX}' (took={took}ms).")
    print("All carry record_source = 'synthetic_probe'.")
    print()
    print("Next:  uv run python scripts/probe_rrf_scores.py")
    print("Then:  uv run python scripts/cleanup_probe_patterns.py")


if __name__ == "__main__":
    main()
