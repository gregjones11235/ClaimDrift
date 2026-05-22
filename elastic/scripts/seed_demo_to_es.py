import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(SCRIPT_ROOT))

from ingestion.common.doi import preprint_id
from ingestion.common.elastic import ElasticsearchHttpClient


ROOT = Path(__file__).resolve().parents[1]
DEMO_SEED = ROOT / "demo_seed"
DEMO_RECORD_SOURCE = "demo_seed"


INDEX_ID_GETTERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "affected_citations": lambda row: row["affected_citation_id"],
    "claims": lambda row: row["claim_id"],
    "drift_events": lambda row: row["event_id"],
    "drift_patterns": lambda row: row["pattern_id"],
    "notification_log": lambda row: row["affected_citation_id"],
    "preprints": lambda row: preprint_id(row["doi"], row["version"]),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write ClaimDrift demo seed records into Elasticsearch.")
    parser.add_argument("--apply", action="store_true", help="Write demo seed records to Elasticsearch.")
    parser.add_argument(
        "--index",
        choices=sorted(INDEX_ID_GETTERS),
        action="append",
        help="Only seed one index. Can be passed more than once.",
    )
    return parser


def load_rows(index_name: str) -> list[dict[str, Any]]:
    path = DEMO_SEED / f"{index_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing demo seed file: {path}")
    return json.loads(path.read_text())


def rows_for_index(index_name: str) -> list[tuple[str, dict[str, Any]]]:
    id_getter = INDEX_ID_GETTERS[index_name]
    rows = []
    for row in load_rows(index_name):
        tagged_row = {"record_source": DEMO_RECORD_SOURCE, **row}
        rows.append((id_getter(tagged_row), tagged_row))
    return rows


def main() -> None:
    args = build_parser().parse_args()
    index_names = args.index or sorted(INDEX_ID_GETTERS)

    try:
        prepared = [(index_name, rows_for_index(index_name)) for index_name in index_names]

        if not args.apply:
            print("Dry run. Pass --apply to write demo seed records into Elasticsearch.")
            for index_name, rows in prepared:
                print(f"Would upsert {len(rows)} records into {index_name}")
            return

        client = ElasticsearchHttpClient()
        for index_name, rows in prepared:
            response = client.bulk_index(index_name, rows)
            if response.get("errors"):
                failures = [
                    item
                    for item in response.get("items", [])
                    if item.get("index", {}).get("error")
                ]
                raise RuntimeError(json.dumps(failures[:3], indent=2))
            print(f"Upserted {response.get('count', len(rows))} records into {index_name}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
