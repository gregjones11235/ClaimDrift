from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from .common.elastic import ElasticsearchHttpClient
from .common.doi import preprint_id
from .common.records import crossref_record_from_puller, preprint_record_from_puller, utc_now
from .pullers import BioRxivPuller, CrossrefPuller, MedRxivPuller


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a ClaimDrift data puller.")
    parser.add_argument("--source", required=True, choices=["biorxiv", "medrxiv", "crossref"])
    parser.add_argument("--since", help="Start date for bioRxiv/medRxiv pulls, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, help="Maximum records to pull.")
    parser.add_argument("--doi", help="DOI to look up when --source crossref is used.")
    parser.add_argument("--raw", action="store_true", help="Print source-normalized puller payload too.")
    parser.add_argument("--dry-run", action="store_true", help="Print normalized records instead of writing to Elasticsearch.")
    parser.add_argument("--apply", action="store_true", help="Write normalized preprint records to Elasticsearch.")
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


def run(args: argparse.Namespace) -> Dict[str, Any]:
    if args.source == "biorxiv":
        result = BioRxivPuller().run_pull("biorxiv", since=args.since, limit=args.limit)
        ts = utc_now()
        normalized = [preprint_record_from_puller(row, ingested_at=ts) for row in result["payload"]]
    elif args.source == "medrxiv":
        result = MedRxivPuller().run_pull("medrxiv", since=args.since, limit=args.limit)
        ts = utc_now()
        normalized = [preprint_record_from_puller(row, ingested_at=ts) for row in result["payload"]]
    else:
        if not args.doi:
            raise ValueError("--doi is required when --source crossref is used.")
        result = CrossrefPuller().run_pull(args.doi, limit=args.limit)
        normalized = [crossref_record_from_puller(row) for row in result["payload"]]

    upserted = 0
    if args.apply:
        if args.source == "crossref":
            raise ValueError("--apply currently supports biorxiv and medrxiv preprint writes only.")
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
