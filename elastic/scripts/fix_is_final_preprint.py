"""One-shot fixer: repair is_final_preprint on existing preprints rows.

Background (contracts.md §2.2.1):
  is_final_preprint must be TRUE on exactly one row per DOI — the latest
  v\\d+ preprint version, and only when published_doi is set. The
  version=published row, all non-latest v\\d+ rows, and any row without
  published_doi must be FALSE.

Bug history:
  Pre-fix puller (ingestion/common/records.py:82) wrote
  is_final_preprint = bool(published_doi) for EVERY version, so v1 / v2
  / v3 of the same DOI could all be true simultaneously. The dispatcher's
  version picker (apps/dispatcher/main.py:539) papered over this with a
  posted_date desc tiebreaker, but the field itself remained meaningless.
  This script reconciles existing rows to the corrected semantics.

What this script does:
  1. Aggregate distinct preprint_doi values in the `preprints` index where
     record_source != "demo_seed" (real puller-ingested data only —
     demo seed is already correct).
  2. For each DOI, find its v\\d+ rows ordered by posted_date desc,
     version desc. Pick the first one as the target.
  3. Use update_by_query to force is_final_preprint=false on EVERY row of
     that DOI (including the published row), then PUT
     is_final_preprint=true on the target row IFF published_doi is set
     on it.
  4. Report counts: dois_processed, target_flipped_true, rows_forced_false.

Safety:
  - Idempotent: running it twice yields the same final state.
  - Read-write but scoped to one field; never touches other columns,
    other indices, or demo seed data.
  - Pass --dry-run to see what would change without writing.

Usage (WSL):
  ELASTIC_ENDPOINT=https://... ELASTIC_API_KEY=... \\
    uv run python elastic/scripts/fix_is_final_preprint.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _request(method: str, endpoint: str, api_key: str, path: str, body: dict | None) -> dict:
    url = f"{endpoint.rstrip('/')}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_preview = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {body_preview}") from e


def list_offending_dois(endpoint: str, api_key: str) -> list[str]:
    """Return DOIs that violate the §2.2.1 invariant (real puller-ingested
    data only). Two flavors of offense are covered:

      (A) DOI has >1 row with is_final_preprint=true (the original bug —
          v1+v2+...+vN all true)
      (B) DOI has v\\d+ rows with published_doi set, but ZERO of them have
          is_final_preprint=true (damage caused by an aborted earlier run of
          this fixer that flipped them all false in step 1 but failed at
          step 3 — e.g. a URL-encoding bug)

    We don't scan the full ~10k distinct DOIs because the vast majority
    (only-v1, or no published_doi) are already correct. Both queries are
    targeted aggregations.

    Returns deduplicated DOI list.
    """
    offenders: set[str] = set()

    # Flavor A: >1 true row per DOI.
    after: dict | None = None
    while True:
        agg_body: dict[str, Any] = {
            "size": 0,
            "query": {
                "bool": {
                    "must_not": [{"term": {"record_source": "demo_seed"}}],
                    "filter": [{"term": {"is_final_preprint": True}}],
                }
            },
            "aggs": {
                "by_doi": {
                    "composite": {
                        "size": 1000,
                        "sources": [{"doi": {"terms": {"field": "doi"}}}],
                    }
                }
            },
        }
        if after is not None:
            agg_body["aggs"]["by_doi"]["composite"]["after"] = after
        resp = _request("POST", endpoint, api_key, "/preprints/_search", agg_body)
        buckets = resp.get("aggregations", {}).get("by_doi", {}).get("buckets", [])
        if not buckets:
            break
        for b in buckets:
            # composite agg has no min_doc_count; filter client-side.
            if b.get("doc_count", 0) >= 2:
                offenders.add(b["key"]["doi"])
        after = resp["aggregations"]["by_doi"].get("after_key")
        if after is None:
            break

    # Flavor B: DOI has v\d+ rows with published_doi but no row is true.
    # We aggregate every DOI with v\d+ rows that have published_doi, then
    # for each bucket count how many of those rows are is_final_preprint=true.
    # buckets with any_true.doc_count == 0 are damaged.
    after = None
    while True:
        agg_body = {
            "size": 0,
            "query": {
                "bool": {
                    "must_not": [
                        {"term": {"record_source": "demo_seed"}},
                        {"term": {"version": "published"}},
                    ],
                    "filter": [{"exists": {"field": "published_doi"}}],
                }
            },
            "aggs": {
                "by_doi": {
                    "composite": {
                        "size": 1000,
                        "sources": [{"doi": {"terms": {"field": "doi"}}}],
                    },
                    "aggs": {
                        "any_true": {
                            "filter": {"term": {"is_final_preprint": True}}
                        }
                    },
                }
            },
        }
        if after is not None:
            agg_body["aggs"]["by_doi"]["composite"]["after"] = after
        resp = _request("POST", endpoint, api_key, "/preprints/_search", agg_body)
        buckets = resp.get("aggregations", {}).get("by_doi", {}).get("buckets", [])
        if not buckets:
            break
        for b in buckets:
            if b["any_true"]["doc_count"] == 0:
                offenders.add(b["key"]["doi"])
        after = resp["aggregations"]["by_doi"].get("after_key")
        if after is None:
            break

    return sorted(offenders)


def find_target_row(endpoint: str, api_key: str, doi: str) -> dict | None:
    """For one DOI, find the highest-version v\\d+ row. Returns the _id and
    fields needed for the decision, or None if the DOI has no v\\d+ row."""
    body = {
        "size": 1,
        "_source": ["doi", "version", "is_final_preprint", "published_doi", "posted_date"],
        "query": {
            "bool": {
                "filter": [{"term": {"doi": doi}}],
                "must_not": [{"term": {"version": "published"}}],
            }
        },
        "sort": [
            {"posted_date": {"order": "desc", "missing": "_last"}},
            {"version": {"order": "desc"}},
        ],
    }
    resp = _request("POST", endpoint, api_key, "/preprints/_search", body)
    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        return None
    hit = hits[0]
    return {"_id": hit["_id"], **hit.get("_source", {})}


def force_all_false(endpoint: str, api_key: str, doi: str) -> int:
    """update_by_query: set is_final_preprint=false on every row of this DOI."""
    body = {
        "script": {
            "source": "if (ctx._source.is_final_preprint != false) { ctx._source.is_final_preprint = false; } else { ctx.op = 'noop'; }",
            "lang": "painless",
        },
        "query": {"term": {"doi": doi}},
    }
    resp = _request(
        "POST",
        endpoint,
        api_key,
        "/preprints/_update_by_query?refresh=true&conflicts=proceed",
        body,
    )
    return int(resp.get("updated", 0))


def flip_target_true(endpoint: str, api_key: str, target_id: str) -> bool:
    """POST _update on a specific row, setting is_final_preprint=true.
    Returns True if the row was changed.

    The preprints index uses composite doc_ids like
    "10.1101/2021.03.29.437597::v4" which contain "/" — these MUST be
    percent-encoded in the URL path, otherwise ES routes the request as
    /preprints/_update/10.1101/2021... (extra path segments) and returns
    HTTP 400 "no handler found for uri ...".
    """
    encoded_id = urllib.parse.quote(target_id, safe="")
    body = {"doc": {"is_final_preprint": True}}
    resp = _request(
        "POST",
        endpoint,
        api_key,
        f"/preprints/_update/{encoded_id}?refresh=true",
        body,
    )
    return resp.get("result") in ("updated", "noop")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile is_final_preprint per contracts.md §2.2.1.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; do not write.")
    parser.add_argument("--limit", type=int, default=None, help="Cap DOIs processed (debugging).")
    args = parser.parse_args()

    endpoint = os.environ.get("ELASTIC_ENDPOINT")
    api_key = os.environ.get("ELASTIC_API_KEY")
    if not endpoint or not api_key:
        print("ERROR: set ELASTIC_ENDPOINT and ELASTIC_API_KEY in env", file=sys.stderr)
        return 2

    print(f"{'DRY-RUN: ' if args.dry_run else ''}Reconciling is_final_preprint against {endpoint}\n")

    dois = list_offending_dois(endpoint, api_key)
    if args.limit is not None:
        dois = dois[: args.limit]
    print(
        f"Found {len(dois)} DOIs violating §2.2.1 "
        "(>1 row with is_final_preprint=true OR has published_doi but no row is true).\n"
    )
    if not dois:
        print("Nothing to fix — invariant already holds.")
        return 0

    stats = {
        "dois_processed": 0,
        "dois_with_no_vN_row": 0,
        "dois_target_eligible_for_true": 0,  # target row has published_doi
        "dois_target_ineligible": 0,         # target row has no published_doi -> stays false
        "rows_forced_false_total": 0,
        "targets_flipped_true": 0,
        "errors": 0,
    }

    for i, doi in enumerate(dois, 1):
        try:
            target = find_target_row(endpoint, api_key, doi)
            stats["dois_processed"] += 1

            if target is None:
                stats["dois_with_no_vN_row"] += 1
                if args.dry_run:
                    print(f"[{i}/{len(dois)}] {doi}: no v\\d+ row, skipping")
                continue

            has_published = bool(target.get("published_doi"))
            currently_true = bool(target.get("is_final_preprint"))

            if args.dry_run:
                action = "flip-true" if has_published else "leave-false"
                print(
                    f"[{i}/{len(dois)}] {doi}: target _id={target['_id']} "
                    f"version={target.get('version')} published_doi={bool(target.get('published_doi'))} "
                    f"current_is_final={currently_true} -> {action}"
                )
                if has_published:
                    stats["dois_target_eligible_for_true"] += 1
                else:
                    stats["dois_target_ineligible"] += 1
                continue

            # Step 1: force every row of this DOI to false.
            forced = force_all_false(endpoint, api_key, doi)
            stats["rows_forced_false_total"] += forced

            # Step 2: if target has published_doi, flip it true.
            if has_published:
                flip_target_true(endpoint, api_key, target["_id"])
                stats["targets_flipped_true"] += 1
                stats["dois_target_eligible_for_true"] += 1
            else:
                stats["dois_target_ineligible"] += 1

            if i % 50 == 0:
                print(f"...progress {i}/{len(dois)}", flush=True)

        except Exception as e:
            stats["errors"] += 1
            print(f"[{i}/{len(dois)}] {doi}: ERROR {e}", file=sys.stderr)

    print("\nDone.")
    print(json.dumps(stats, indent=2))
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
