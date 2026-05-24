from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from .common.elastic import ElasticsearchHttpClient
from .common.doi import preprint_id
from .common.records import (
    affected_citation_candidate_from_openalex,
    crossref_record_from_puller,
    preprint_record_from_puller,
    utc_now,
)
from .pullers import BioRxivPuller, CrossrefPuller, MedRxivPuller, OpenAlexClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a ClaimDrift data puller.")
    parser.add_argument("--source", required=True, choices=["biorxiv", "medrxiv", "crossref", "openalex"])
    parser.add_argument("--since", help="Start date for bioRxiv/medRxiv pulls, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, help="Maximum records to pull.")
    parser.add_argument("--doi", help="DOI to look up when --source crossref is used.")
    parser.add_argument("--drift-event-id", help="Drift event id for OpenAlex affected-citation candidate writes.")
    parser.add_argument("--raw", action="store_true", help="Print source-normalized puller payload too.")
    parser.add_argument("--dry-run", action="store_true", help="Print normalized records instead of writing to Elasticsearch.")
    parser.add_argument("--apply", action="store_true", help="Write supported normalized records to Elasticsearch.")
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


def upsert_affected_citations(rows: list[Dict[str, Any]]) -> int:
    client = ElasticsearchHttpClient()
    bulk_rows = [
        (row["affected_citation_id"], row)
        for row in rows
        if row.get("affected_citation_id")
    ]
    response = client.bulk_index("affected_citations", bulk_rows)
    if response.get("errors"):
        raise RuntimeError(json.dumps(response.get("items", [])[:3], indent=2))
    return int(response.get("count", len(bulk_rows)))


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
        if args.drift_event_id:
            ts = utc_now()
            normalized = [
                affected_citation_candidate_from_openalex(row, args.drift_event_id, scored_at=ts)
                for row in result["payload"]
            ]
        elif args.apply:
            raise ValueError("--drift-event-id is required when applying OpenAlex candidates.")
        else:
            normalized = result["payload"]

    upserted = 0
    if args.apply:
        if args.source == "crossref":
            raise ValueError("--apply currently supports biorxiv/medrxiv preprint writes and openalex candidate writes.")
        if args.source == "openalex":
            upserted = upsert_affected_citations(normalized)
        else:
            upserted = upsert_preprints(normalized)

    output: Dict[str, Any] = {
        "source": args.source,
        "mode": "apply" if args.apply else "dry_run" if args.dry_run else "preview",
        "fetched": result["fetched"],
        "upserted": upserted,
        "would_upsert": 0 if args.apply or args.source == "crossref" else len(normalized),
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
