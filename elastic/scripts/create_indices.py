import argparse
import json
from pathlib import Path
import sys
from urllib.parse import quote


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(SCRIPT_ROOT))

from ingestion.common.elastic import ElasticsearchHttpClient


ROOT = Path(__file__).resolve().parents[1]
MAPPINGS = ROOT / "mappings"
STALE_DEFAULT_PIPELINE_INDEXES = {"claims", "drift_patterns", "preprints"}

# The curator shadow index is NOT a bootstrap index: its lifecycle is owned by
# the pattern_curator blue-green rebuild (elastic/scripts/manage_pattern_alias.py
# `rebuild`, which drops+recreates it each run). Creating it here would bind its
# pattern_description to the claimdrift-elser-batch endpoint prematurely and leave
# an empty index lying around. Skip it during the general index bootstrap.
SKIP_INDEXES = {"drift_patterns_v2"}


def normalize_index_settings(settings: dict) -> dict:
    normalized = {}
    for key, value in settings.items():
        normalized[key.removeprefix("index.")] = value
    return normalized


def update_existing_index_settings(client: ElasticsearchHttpClient, index_name: str, settings: dict) -> None:
    normalized = normalize_index_settings(settings)
    if index_name in STALE_DEFAULT_PIPELINE_INDEXES:
        normalized["default_pipeline"] = "_none"
    if normalized:
        client.request("PUT", f"/{quote(index_name)}/_settings", {"index": normalized})


def update_existing_index_mapping(client: ElasticsearchHttpClient, index_name: str, mapping: dict) -> None:
    properties = mapping.get("properties", {})
    if properties:
        client.request("PUT", f"/{quote(index_name)}/_mapping", {"properties": properties})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create ClaimDrift Elasticsearch pipeline and indexes.")
    parser.add_argument("--apply", action="store_true", help="Create resources in Elasticsearch.")
    parser.add_argument("--skip-existing", action="store_true", help="Continue if an index already exists.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        mappings = [
            (path.stem, json.loads(path.read_text()))
            for path in sorted(MAPPINGS.glob("*.json"))
            if path.stem not in SKIP_INDEXES
        ]

        if not args.apply:
            print("Dry run. Pass --apply to create resources in Elasticsearch.")
            print()
            print("No ingest pipeline is created.")
            print("  semantic_text fields explicitly use the .elser-2-elastic inference endpoint.")
            print()

            for index_name, body in mappings:
                properties = body.get("mappings", {}).get("properties", {})
                print(f"Would create index: {index_name}")
                print(f"  fields: {', '.join(properties.keys())}")
            return

        client = ElasticsearchHttpClient()
        print("Skipped ingest pipeline creation for semantic_text ELSER mode.")

        for index_name, body in mappings:
            properties = body.get("mappings", {}).get("properties", {})
            try:
                client.put_index(index_name, body)
                print(f"Created index: {index_name}")
            except RuntimeError as exc:
                if args.skip_existing and "resource_already_exists_exception" in str(exc):
                    print(f"Skipped existing index: {index_name}")
                    settings = body.get("settings", {})
                    update_existing_index_settings(client, index_name, settings)
                    update_existing_index_mapping(client, index_name, body.get("mappings", {}))
                    updated_keys = list(settings.keys())
                    if index_name in STALE_DEFAULT_PIPELINE_INDEXES:
                        updated_keys.append("index.default_pipeline=_none")
                    print(f"  updated settings: {', '.join(updated_keys)}")
                    print("  updated mapping properties")
                    continue
                raise
            print(f"  fields: {', '.join(properties.keys())}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
