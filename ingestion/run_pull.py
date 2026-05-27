from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from typing import Any, Dict, Optional

from .common.elastic import ElasticsearchHttpClient
from .common.doi import preprint_id
from .common.records import (
    crossref_record_from_puller,
    preprint_record_from_puller,
    published_record_from_crossref,
    utc_now,
)
from .pullers import ArxivPuller, BioRxivPuller, CrossrefPuller, MedRxivPuller, OpenAlexClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a ClaimDrift data puller.")
    parser.add_argument(
        "--source",
        required=True,
        choices=["arxiv", "biorxiv", "medrxiv", "crossref", "crossref-batch", "openalex"],
    )
    parser.add_argument("--since", help="Start date for arXiv/bioRxiv/medRxiv pulls, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, help="Maximum records to pull.")
    parser.add_argument("--doi", help="DOI to look up when --source crossref is used.")
    parser.add_argument(
        "--preprint-source",
        choices=["biorxiv", "medrxiv"],
        default="medrxiv",
        help="Source label to use when creating a published preprints row from Crossref.",
    )
    parser.add_argument(
        "--batch-source",
        choices=["all", "biorxiv", "medrxiv"],
        default="all",
        help="Source filter for --source crossref-batch.",
    )
    parser.add_argument("--raw", action="store_true", help="Print source-normalized puller payload too.")
    parser.add_argument("--include-items", action="store_true", help="Print normalized output items.")
    parser.add_argument("--dry-run", action="store_true", help="Print normalized records instead of writing to Elasticsearch.")
    parser.add_argument("--apply", action="store_true", help="Write normalized preprint records to Elasticsearch.")
    parser.add_argument(
        "--include-published",
        action="store_true",
        help="When source records contain published_doi, also create version=published rows via Crossref.",
    )
    parser.add_argument(
        "--bulk-batch-size",
        type=int,
        default=500,
        help="Maximum Elasticsearch documents per bulk request.",
    )
    parser.add_argument(
        "--arxiv-set",
        default="q-bio",
        help="arXiv OAI-PMH set to pull when --source arxiv is used. Use '' for all sets.",
    )
    return parser


def _chunked(rows: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        raise ValueError("--bulk-batch-size must be greater than 0.")
    return [rows[index:index + size] for index in range(0, len(rows), size)]


def upsert_preprints(rows: list[Dict[str, Any]], batch_size: int = 500) -> int:
    client = ElasticsearchHttpClient()
    bulk_rows = [
        (preprint_id(row["doi"], row["version"]), row)
        for row in rows
        if row.get("doi") and row.get("version")
    ]
    upserted = 0
    for batch in _chunked(bulk_rows, batch_size):
        response = client.bulk_index("preprints", batch)
        if response.get("errors"):
            raise RuntimeError(json.dumps(response.get("items", [])[:3], indent=2))
        upserted += int(response.get("count", len(batch)))
    return upserted


def update_preprint_published_doi(preprint_doi: str, published_doi: str) -> int:
    """Pair a preprint DOI with its published DOI, and (per contracts.md §2.2.1)
    set is_final_preprint=true on EXACTLY ONE row — the highest v\\d+ version
    of this DOI. All other rows of the same DOI (older versions + the
    version=published row) are forced to false in the same pass.

    Returns the number of rows touched by the update_by_query (the bulk pass);
    the targeted true-flip is one additional PUT and is not included in this
    count because callers historically treat the return as "rows updated by
    this pairing", and the bulk update_by_query is the closer analog of the
    pre-fix behavior.
    """
    client = ElasticsearchHttpClient()

    # Step 1: set published_doi on every row of this DOI; force is_final_preprint
    # to false unconditionally. The single true-flip happens in step 3.
    response = client.request(
        "POST",
        "/preprints/_update_by_query?refresh=true&conflicts=proceed",
        {
            "script": {
                "source": "ctx._source.published_doi = params.published_doi; ctx._source.is_final_preprint = false",
                "lang": "painless",
                "params": {"published_doi": published_doi},
            },
            "query": {"term": {"doi": preprint_doi}},
        },
    )
    updated = int(response.get("updated", 0))

    # Step 2: find the highest v\d+ version row for this DOI (exclude the
    # version=published row). If there is no v\d+ row (e.g. only a published
    # row exists yet — race condition during ingestion), there is nothing to
    # flip true and we return.
    search_resp = client.request(
        "POST",
        "/preprints/_search",
        {
            "size": 1,
            "_source": False,
            "query": {
                "bool": {
                    "filter": [{"term": {"doi": preprint_doi}}],
                    "must_not": [{"term": {"version": "published"}}],
                }
            },
            # version is a keyword like "v1", "v2", ..., "v10". Lexicographic
            # sort is wrong for two-digit versions; sort by posted_date desc
            # then version desc as a stable tiebreaker. posted_date is set per
            # version on bioRxiv/medRxiv (newer versions have later dates).
            "sort": [
                {"posted_date": {"order": "desc", "missing": "_last"}},
                {"version": {"order": "desc"}},
            ],
        },
    )
    hits = search_resp.get("hits", {}).get("hits", [])
    if not hits:
        return updated

    target_id = hits[0]["_id"]

    # Step 3: flip is_final_preprint=true on that single row.
    # preprints doc_ids are composite "{normalized_doi}::{version}" and contain
    # "/" (e.g. "10.1101/2021.03.29.437597::v4"). Percent-encode the whole id
    # so ES routes the URL correctly — otherwise the "/" splits the path and
    # ES returns "no handler found for uri" (see ElasticsearchHttpClient.request
    # which does NOT auto-encode path segments).
    encoded_id = urllib.parse.quote(target_id, safe="")
    client.request(
        "POST",
        f"/preprints/_update/{encoded_id}?refresh=true",
        {"doc": {"is_final_preprint": True}},
    )
    return updated


def fetch_unpaired_preprints(limit: Optional[int], source: Optional[str] = None) -> list[Dict[str, Any]]:
    client = ElasticsearchHttpClient()
    filters: list[Dict[str, Any]] = [{"exists": {"field": "doi"}}]
    if source:
        filters.append({"term": {"source": source}})

    response = client.request(
        "POST",
        "/preprints/_search",
        {
            "size": limit or 25,
            "_source": ["doi", "source", "version", "title", "ingested_at"],
            "sort": [{"ingested_at": {"order": "asc", "missing": "_last"}}],
            "query": {
                "bool": {
                    "filter": filters,
                    "must_not": [
                        {"exists": {"field": "published_doi"}},
                        {"term": {"version": "published"}},
                        {"term": {"record_source": "demo_seed"}},
                    ],
                }
            },
        },
    )
    return [hit.get("_source", {}) for hit in response.get("hits", {}).get("hits", [])]


def build_published_rows(rows: list[Dict[str, Any]], source: str) -> list[Dict[str, Any]]:
    published_rows = []
    ts = utc_now()
    for row in rows:
        published_doi = row.get("published_doi")
        if not published_doi:
            continue
        published_result = CrossrefPuller().run_pull(published_doi, limit=1)
        if published_result["payload"]:
            published_row = crossref_record_from_puller(published_result["payload"][0])
        else:
            published_row = {**row, "doi": published_doi, "published_doi": published_doi}
        published_rows.append(
            published_record_from_crossref(
                published_row,
                source=source,
                ingested_at=ts,
            )
        )
    return published_rows


def pair_preprints_with_crossref(
    candidates: list[Dict[str, Any]],
    *,
    apply: bool,
    bulk_batch_size: int = 500,
) -> Dict[str, Any]:
    crossref = CrossrefPuller()
    paired: list[Dict[str, Any]] = []
    no_match: list[str] = []
    errors: list[str] = []
    published_upserted = 0
    updated_preprints = 0

    for candidate in candidates:
        doi = candidate.get("doi")
        source = candidate.get("source") or "unknown"
        if not doi:
            continue

        lookup = crossref.run_pull(doi, limit=1)
        errors.extend(lookup.get("errors", []))
        normalized = [crossref_record_from_puller(row) for row in lookup.get("payload", [])]
        match = next((row for row in normalized if row.get("published_doi")), None)
        if not match:
            no_match.append(doi)
            continue

        paired.append(
            {
                "preprint_doi": doi,
                "preprint_source": source,
                "published_doi": match["published_doi"],
            }
        )
        if apply:
            published_rows = build_published_rows([match], source=source)
            published_upserted += upsert_preprints(published_rows, batch_size=bulk_batch_size)
            updated_preprints += update_preprint_published_doi(doi, match["published_doi"])

    return {
        "processed": len(candidates),
        "paired": len(paired),
        "published_upserted": published_upserted,
        "updated_preprints": updated_preprints,
        "skipped_without_match": len(no_match),
        "no_match_dois": no_match,
        "errors": errors,
        "items": paired,
    }


def run(args: argparse.Namespace) -> Dict[str, Any]:
    if args.source == "arxiv":
        arxiv_set = args.arxiv_set or None
        result = ArxivPuller().run_pull("arxiv", since=args.since, limit=args.limit, arxiv_set=arxiv_set)
        ts = utc_now()
        normalized = [preprint_record_from_puller(row, ingested_at=ts) for row in result["payload"]]
    elif args.source == "biorxiv":
        result = BioRxivPuller().run_pull("biorxiv", since=args.since, limit=args.limit)
        ts = utc_now()
        normalized = [preprint_record_from_puller(row, ingested_at=ts) for row in result["payload"]]
    elif args.source == "medrxiv":
        result = MedRxivPuller().run_pull("medrxiv", since=args.since, limit=args.limit)
        ts = utc_now()
        normalized = [preprint_record_from_puller(row, ingested_at=ts) for row in result["payload"]]
    elif args.source == "crossref":
        if not args.doi:
            raise ValueError("--doi is required when --source crossref is used.")
        result = CrossrefPuller().run_pull(args.doi, limit=args.limit)
        normalized = [crossref_record_from_puller(row) for row in result["payload"]]
    elif args.source == "crossref-batch":
        source = None if args.batch_source == "all" else args.batch_source
        candidates = fetch_unpaired_preprints(args.limit, source=source)
        batch_result = pair_preprints_with_crossref(
            candidates,
            apply=args.apply,
            bulk_batch_size=args.bulk_batch_size,
        )
        return {
            "source": args.source,
            "mode": "apply" if args.apply else "dry_run" if args.dry_run else "preview",
            "batch_source": args.batch_source,
            "fetched": len(candidates),
            "upserted": batch_result["published_upserted"],
            "published_upserted": batch_result["published_upserted"],
            "updated_preprints": batch_result["updated_preprints"],
            "would_upsert": 0 if args.apply else batch_result["paired"],
            "skipped": batch_result["skipped_without_match"],
            "errors": batch_result["errors"],
            "processed": batch_result["processed"],
            "paired": batch_result["paired"],
            "no_match_count": len(batch_result["no_match_dois"]),
            **({"no_match_dois": batch_result["no_match_dois"]} if args.include_items else {}),
            **({"items": batch_result["items"]} if args.include_items else {}),
        }
    else:
        if not args.doi:
            raise ValueError("--doi is required when --source openalex is used.")
        result = OpenAlexClient().run_pull(args.doi, limit=args.limit)
        normalized = result["payload"]

    upserted = 0
    published_upserted = 0
    updated_preprints = 0
    if args.apply:
        if args.source == "openalex":
            raise ValueError("--apply currently supports biorxiv/medrxiv preprint writes and crossref pairing.")
        if args.source == "crossref":
            published_rows = build_published_rows(normalized, source=args.preprint_source)
            published_upserted = upsert_preprints(published_rows, batch_size=args.bulk_batch_size)
            for row in normalized:
                if row.get("published_doi"):
                    updated_preprints += update_preprint_published_doi(args.doi, row["published_doi"])
            upserted = published_upserted
        else:
            upserted = upsert_preprints(normalized, batch_size=args.bulk_batch_size)
            if args.include_published:
                published_rows = build_published_rows(normalized, source=args.source)
                published_upserted = upsert_preprints(published_rows, batch_size=args.bulk_batch_size)

    output: Dict[str, Any] = {
        "source": args.source,
        "mode": "apply" if args.apply else "dry_run" if args.dry_run else "preview",
        "fetched": result["fetched"],
        "upserted": upserted,
        "published_upserted": published_upserted,
        "updated_preprints": updated_preprints,
        "would_upsert": 0 if args.apply or args.source in ("crossref", "openalex") else len(normalized),
        "skipped": result["skipped"],
        "errors": result["errors"],
    }
    if args.include_items:
        output["items"] = normalized
    if args.raw:
        output["raw_items"] = result["payload"]
    if args.source == "openalex":
        output["total_found"] = result.get("total_found", len(normalized))
        output["processed"] = result.get("processed", len(normalized))
        output["skipped_without_doi"] = result.get("skipped_without_doi", 0)
        output["source_openalex_id"] = result.get("source_openalex_id")
    return output


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        output = run(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
