import argparse
import json
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(SCRIPT_ROOT))

from ingestion.common.elastic import ElasticsearchHttpClient


ROOT = Path(__file__).resolve().parents[1]
MAPPINGS = ROOT / "mappings"
INGEST_PIPELINES = ROOT / "pipelines"
PIPELINE_ID = "claimdrift_elser_v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create ClaimDrift Elasticsearch pipeline and indexes.")
    parser.add_argument("--apply", action="store_true", help="Create resources in Elasticsearch.")
    parser.add_argument("--skip-existing", action="store_true", help="Continue if an index already exists.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        pipeline = json.loads((INGEST_PIPELINES / "elser_ingest_pipeline.json").read_text())
        mappings = [(path.stem, json.loads(path.read_text())) for path in sorted(MAPPINGS.glob("*.json"))]

        if not args.apply:
            print("Dry run. Pass --apply to create resources in Elasticsearch.")
            print()
            print(f"Would create ingest pipeline: {PIPELINE_ID}")
            print(f"  processors: {len(pipeline.get('processors', []))}")
            print()

            for index_name, body in mappings:
                properties = body.get("mappings", {}).get("properties", {})
                print(f"Would create index: {index_name}")
                print(f"  fields: {', '.join(properties.keys())}")
            return

        client = ElasticsearchHttpClient()
        client.put_pipeline(PIPELINE_ID, pipeline)
        print(f"Created ingest pipeline: {PIPELINE_ID}")

        for index_name, body in mappings:
            properties = body.get("mappings", {}).get("properties", {})
            try:
                client.put_index(index_name, body)
                print(f"Created index: {index_name}")
            except RuntimeError as exc:
                if args.skip_existing and "resource_already_exists_exception" in str(exc):
                    print(f"Skipped existing index: {index_name}")
                    continue
                raise
            print(f"  fields: {', '.join(properties.keys())}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
