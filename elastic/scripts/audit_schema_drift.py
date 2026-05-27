"""Three-way schema audit: mapping JSON ↔ live ES cluster ↔ demo seed JSON.

For each of the 6 indices defined in contracts.md §2.2, this script checks:

  (A) mapping JSON file        (elastic/mappings/<idx>.json)
        vs.
  (B) live cluster mapping     (GET /<idx>/_mapping)
        vs.
  (C) demo seed JSON           (elastic/demo_seed/<idx>.json)

Reports any field set diff per index. Exit code is non-zero if ANY drift is
detected — suitable for use as a pre-commit / CI sanity gate.

Why this matters: contracts.md changelog 2026-05-25 captures a real schema
drift incident where `drift_events` mapping JSON, live cluster, and seed JSON
all disagreed on the shape of `retrieved_patterns` (was nested, contract said
keyword; `analyzed_at` was missing from the mapping JSON entirely). That
silently broke writes from the dispatcher until caught. Mapping changes must
batch (a) JSON file, (b) live cluster, (c) demo seed JSON, (d) changelog;
this audit script enforces the first three.

Scope notes:
  - We compare top-level property NAMES only (per-index). Type checks are
    out-of-scope for v0 because nested-property comparisons are noisy and
    the field-name set is where real-world drift bites first.
  - `record_source` is a contract-mandated field on every index (§2.3); we
    assert its presence everywhere as a bonus invariant.
  - Demo seed files are arrays of documents; we union all keys observed
    across all documents so empty/optional fields don't trigger false drift.

Usage (WSL):
  ELASTIC_ENDPOINT=https://... ELASTIC_API_KEY=... \\
    uv run python elastic/scripts/audit_schema_drift.py

Read-only — no writes, safe to run any time.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

# Indices listed in contracts.md §2.1 (in stable order). Indices with
# `has_demo_seed=False` skip the seed-JSON arm of the audit — they hold
# operational state (e.g. `dispatch_state` is the scheduled-workflow
# watermark), not business demo data.
INDICES: list[tuple[str, bool]] = [
    ("preprints",          True),
    ("claims",             True),
    ("drift_events",       True),
    ("affected_citations", True),
    ("drift_patterns",     True),
    ("notification_log",   True),
    ("dispatch_state",     False),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPINGS_DIR = REPO_ROOT / "elastic" / "mappings"
DEMO_SEED_DIR = REPO_ROOT / "elastic" / "demo_seed"


def _load_mapping_json(index: str) -> set[str]:
    """Read the static mapping JSON and return its top-level field name set."""
    path = MAPPINGS_DIR / f"{index}.json"
    if not path.exists():
        raise FileNotFoundError(f"mapping JSON not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    props = (data.get("mappings") or {}).get("properties") or {}
    return set(props.keys())


def _load_demo_seed_keys(index: str) -> set[str]:
    """Read all docs from demo seed JSON, return UNION of every top-level key
    observed across all documents."""
    path = DEMO_SEED_DIR / f"{index}.json"
    if not path.exists():
        raise FileNotFoundError(f"demo seed JSON not found: {path}")
    docs = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(docs, list):
        raise ValueError(f"{path}: expected an array of docs, got {type(docs).__name__}")
    keys: set[str] = set()
    for doc in docs:
        if isinstance(doc, dict):
            keys.update(doc.keys())
    return keys


def _fetch_live_mapping(index: str, endpoint: str, api_key: str) -> set[str]:
    """GET /<idx>/_mapping; return live top-level property names."""
    url = f"{endpoint.rstrip('/')}/{index}/_mapping"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"ApiKey {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GET {url} -> HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}") from e
    # Response shape: {"<index>": {"mappings": {"properties": {...}}}}
    inner = payload.get(index) or {}
    props = (inner.get("mappings") or {}).get("properties") or {}
    return set(props.keys())


def _format_diff(label: str, fields: Iterable[str]) -> str:
    fields = sorted(fields)
    if not fields:
        return ""
    return f"    {label}: {', '.join(fields)}"


def audit_index(index: str, endpoint: str, api_key: str, has_demo_seed: bool) -> list[str]:
    """Audit one index. Returns a list of drift messages; empty list = clean."""
    messages: list[str] = []
    try:
        mapping_keys = _load_mapping_json(index)
    except Exception as e:
        return [f"  [ERROR] cannot load mapping JSON: {e}"]
    if has_demo_seed:
        try:
            seed_keys = _load_demo_seed_keys(index)
        except Exception as e:
            return [f"  [ERROR] cannot load demo seed JSON: {e}"]
    else:
        seed_keys = set()
    try:
        live_keys = _fetch_live_mapping(index, endpoint, api_key)
    except Exception as e:
        return [f"  [ERROR] cannot fetch live mapping: {e}"]

    # Bonus invariant: record_source must exist in mapping + live (§2.3).
    if "record_source" not in mapping_keys:
        messages.append("  [INVARIANT] mapping JSON is missing `record_source` (required by §2.3)")
    if "record_source" not in live_keys:
        messages.append("  [INVARIANT] live cluster is missing `record_source` (required by §2.3)")

    # Pairwise diffs.
    json_minus_live = mapping_keys - live_keys
    live_minus_json = live_keys - mapping_keys
    if json_minus_live or live_minus_json:
        messages.append("  [JSON ↔ LIVE drift]")
        if json_minus_live:
            messages.append(_format_diff("only in mapping JSON", json_minus_live))
        if live_minus_json:
            messages.append(_format_diff("only in live cluster", live_minus_json))

    if has_demo_seed:
        # Seed keys must be a SUBSET of mapping (because `dynamic: strict` would
        # reject unknown keys at index time). Extra seed keys ⇒ bulk would fail.
        seed_minus_mapping = seed_keys - mapping_keys
        if seed_minus_mapping:
            messages.append("  [SEED ↔ JSON drift] demo seed has keys not in mapping (would fail strict-dynamic bulk):")
            messages.append(_format_diff("extra in seed", seed_minus_mapping))

        # Seed keys missing from mapping is fine (optional fields). We DON'T flag
        # mapping fields absent in seed — that's expected (demo seed is small).

    if not messages:
        if has_demo_seed:
            messages.append(
                f"  OK — {len(mapping_keys)} fields in mapping, "
                f"{len(live_keys)} in live, {len(seed_keys)} observed in seed"
            )
        else:
            messages.append(
                f"  OK — {len(mapping_keys)} fields in mapping, "
                f"{len(live_keys)} in live (no demo seed expected; operational state index)"
            )
    return messages


def _es_post(endpoint: str, api_key: str, path: str, body: dict) -> dict:
    """Tiny POST helper for the cross-field semantic invariants below."""
    url = f"{endpoint.rstrip('/')}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_is_final_preprint_invariants(endpoint: str, api_key: str) -> list[str]:
    """Cross-field semantic invariants on the `preprints` index (contracts.md §2.2.1):

      (a) per DOI, at most one version has is_final_preprint=true
      (b) the version=published row must always have is_final_preprint=false

    These are NOT field-presence drift — they are field-VALUE relationships
    that field-name diffs cannot catch. Scope: real puller-ingested data only
    (record_source != "demo_seed"); demo seed is hand-curated and correct.

    Returns a list of drift messages; empty list = clean.
    """
    messages: list[str] = []

    # (a) Aggregate: for each DOI, count is_final_preprint=true rows;
    # bucket_selector filters to those with count > 1.
    agg_body = {
        "size": 0,
        "query": {
            "bool": {
                "must_not": [{"term": {"record_source": "demo_seed"}}],
                "filter": [{"term": {"is_final_preprint": True}}],
            }
        },
        "aggs": {
            "by_doi": {
                "terms": {"field": "doi", "size": 1000},
                "aggs": {
                    "multi_true": {
                        "bucket_selector": {
                            "buckets_path": {"c": "_count"},
                            "script": "params.c > 1",
                        }
                    }
                },
            }
        },
    }
    try:
        resp = _es_post(endpoint, api_key, "/preprints/_search", agg_body)
        offenders = resp.get("aggregations", {}).get("by_doi", {}).get("buckets", [])
        if offenders:
            sample = [(b["key"], b["doc_count"]) for b in offenders[:5]]
            messages.append(
                f"  [INVARIANT §2.2.1] {len(offenders)} DOI(s) have >1 row with is_final_preprint=true "
                f"(sample: {sample})"
            )
    except Exception as e:
        messages.append(f"  [ERROR] could not check is_final_preprint uniqueness invariant: {e}")

    # (b) Any version=published row with is_final_preprint=true is illegal.
    bad_published_body = {
        "size": 0,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must_not": [{"term": {"record_source": "demo_seed"}}],
                "filter": [
                    {"term": {"version": "published"}},
                    {"term": {"is_final_preprint": True}},
                ],
            }
        },
    }
    try:
        resp = _es_post(endpoint, api_key, "/preprints/_search", bad_published_body)
        total = resp.get("hits", {}).get("total", {}).get("value", 0)
        if total > 0:
            messages.append(
                f"  [INVARIANT §2.2.1] {total} row(s) have version=published AND is_final_preprint=true "
                "(version=published must always be false)"
            )
    except Exception as e:
        messages.append(f"  [ERROR] could not check version=published invariant: {e}")

    if not messages:
        messages.append("  OK — is_final_preprint invariants hold (per-DOI uniqueness + published=false)")
    return messages


def main() -> int:
    endpoint = os.environ.get("ELASTIC_ENDPOINT")
    api_key = os.environ.get("ELASTIC_API_KEY")
    if not endpoint or not api_key:
        print("ERROR: set ELASTIC_ENDPOINT and ELASTIC_API_KEY in env", file=sys.stderr)
        return 2

    print(f"Schema-drift audit against {endpoint}\n")
    any_drift = False
    for index, has_demo_seed in INDICES:
        print(f"=== {index} ===")
        messages = audit_index(index, endpoint, api_key, has_demo_seed)
        for m in messages:
            print(m)
            if not m.strip().startswith("OK"):
                any_drift = True
        print()

    # Cross-field semantic invariants (one section per invariant family).
    print("=== preprints: is_final_preprint invariants ===")
    for m in check_is_final_preprint_invariants(endpoint, api_key):
        print(m)
        if not m.strip().startswith("OK"):
            any_drift = True
    print()

    if any_drift:
        print("RESULT: drift detected — see above. Fix by batching mapping JSON + live cluster + demo seed JSON updates (contracts.md §8.1 rule).")
        return 1
    print(f"RESULT: all {len(INDICES)} indices clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
