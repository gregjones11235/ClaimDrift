"""
Delete the synthetic probe patterns injected by inject_probe_patterns.py.

Filter: `record_source == "synthetic_probe"`. The demo seed
(`record_source == "demo_seed"`) is NOT touched. Real puller-ingested
patterns leave `record_source` unset and are NOT touched.

Run from agents/:

    # Dry run (default): show what would be deleted, don't touch anything.
    uv run python scripts/cleanup_probe_patterns.py

    # Actually delete.
    uv run python scripts/cleanup_probe_patterns.py --apply

The dry-run default exists because this script is the second-most-
destructive piece of code in agents/ (after a hypothetical drop-index
script that doesn't exist yet) — easy to misfire if you forget which
record_source value you used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from _shared.elastic_retrieval import DRIFT_PATTERNS_INDEX  # noqa: E402
from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

PROBE_RECORD_SOURCE = "synthetic_probe"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete probe-injected drift_patterns by record_source.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually issue the delete-by-query. Without this, dry-runs (counts only).",
    )
    return parser


def _count_probe_patterns(client: ElasticsearchHttpClient) -> int:
    body = {
        "query": {"term": {"record_source": PROBE_RECORD_SOURCE}},
    }
    response = client.request("POST", f"/{DRIFT_PATTERNS_INDEX}/_count", body)
    return int(response.get("count", 0))


def _list_probe_patterns(client: ElasticsearchHttpClient) -> list[dict]:
    body = {
        "query": {"term": {"record_source": PROBE_RECORD_SOURCE}},
        "_source": ["pattern_id", "pattern_type", "record_source"],
        "size": 100,
    }
    response = client.request("POST", f"/{DRIFT_PATTERNS_INDEX}/_search", body)
    return (response.get("hits") or {}).get("hits") or []


def main() -> None:
    args = _build_parser().parse_args()
    client = ElasticsearchHttpClient()

    matches = _list_probe_patterns(client)
    print(f"Found {len(matches)} pattern(s) with record_source = '{PROBE_RECORD_SOURCE}':")
    for hit in matches:
        source = hit.get("_source", {}) or {}
        print(f"  - {source.get('pattern_id')}  type={source.get('pattern_type')}")

    if not matches:
        print("Nothing to delete.")
        return

    if not args.apply:
        print()
        print("Dry run. Pass --apply to delete these.")
        return

    body = {
        "query": {"term": {"record_source": PROBE_RECORD_SOURCE}},
    }
    # refresh=true so a subsequent count/search reflects the deletion
    # immediately — useful when piping into probe_rrf_scores.py from a
    # shell loop.
    response = client.request(
        "POST",
        f"/{DRIFT_PATTERNS_INDEX}/_delete_by_query?refresh=true",
        body,
    )
    deleted = response.get("deleted", 0)
    failures = response.get("failures") or []
    print(f"Deleted {deleted} document(s).")
    if failures:
        print(f"WARNING: {len(failures)} failure(s) reported by _delete_by_query:")
        for failure in failures:
            print(f"  {failure}")
        raise SystemExit(1)

    remaining = _count_probe_patterns(client)
    if remaining:
        print(f"WARNING: {remaining} probe pattern(s) still present after delete.")
        raise SystemExit(1)
    print("Verified: 0 probe patterns remain.")


if __name__ == "__main__":
    main()
