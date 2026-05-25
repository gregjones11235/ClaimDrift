from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from .common.elastic import ElasticsearchHttpClient
from .common.doi import preprint_id
from .common.records import (
    crossref_record_from_puller,
    preprint_record_from_puller,
    published_record_from_crossref,
    utc_now,
)
from .pullers import BioRxivPuller, CrossrefPuller, MedRxivPuller, OpenAlexClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a ClaimDrift data puller.")
    parser.add_argument("--source", required=True, choices=["biorxiv", "medrxiv", "crossref", "openalex"])
    parser.add_argument("--since", help="Start date for bioRxiv/medRxiv pulls, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, help="Maximum records to pull.")
    parser.add_argument("--doi", help="DOI to look up when --source crossref is used.")
    parser.add_argument(
        "--preprint-source",
        choices=["biorxiv", "medrxiv", "arxiv"],
        default="medrxiv",
        help="Source label to use when creating a published preprints row from Crossref.",
    )
    parser.add_argument("--raw", action="store_true", help="Print source-normalized puller payload too.")
    parser.add_argument("--dry-run", action="store_true", help="Print normalized records instead of writing to Elasticsearch.")
    parser.add_argument("--apply", action="store_true", help="Write normalized preprint records to Elasticsearch.")
    parser.add_argument(
        "--include-published",
        action="store_true",
        help="When source records contain published_doi, also create version=published rows via Crossref.",
    )
    return parser


def upsert_preprints(rows: list[Dict[str, Any]]) -> int:
    client = ElasticsearchHttpClient()
    bulk_rows = [
        (preprint_id(row["doi"], row["version"]), row)
        for row in rows
        if row.get("doi") and row.get("version")
    ]
    response = client.bulk_index("preprints", bulk_rows)
    if response.get("errors"):
        raise RuntimeError(json.dumps(response.get("items", [])[:3], indent=2))
    return int(response.get("count", len(bulk_rows)))


def update_preprint_published_doi(preprint_doi: str, published_doi: str) -> int:
    client = ElasticsearchHttpClient()
    response = client.request(
        "POST",
        "/preprints/_update_by_query?refresh=true&conflicts=proceed",
        {
            "script": {
                "source": "ctx._source.published_doi = params.published_doi; ctx._source.is_final_preprint = true",
                "lang": "painless",
                "params": {"published_doi": published_doi},
            },
            "query": {"term": {"doi": preprint_doi}},
        },
    )
    return int(response.get("updated", 0))


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


def run(args: argparse.Namespace) -> Dict[str, Any]:
    if args.source == "biorxiv":
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
            published_upserted = upsert_preprints(published_rows)
            for row in normalized:
                if row.get("published_doi"):
                    updated_preprints += update_preprint_published_doi(args.doi, row["published_doi"])
            upserted = published_upserted
        else:
            upserted = upsert_preprints(normalized)
            if args.include_published:
                published_rows = build_published_rows(normalized, source=args.source)
                published_upserted = upsert_preprints(published_rows)

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
        "items": normalized,
    }
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
