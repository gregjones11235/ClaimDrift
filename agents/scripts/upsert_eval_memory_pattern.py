#!/usr/bin/env python3
"""Upsert a normalized eval memory pattern into drift_patterns.

This is intentionally deterministic glue around the LLM-produced memory
proposal. The eval artifact keeps machine-owned timestamps as null; this script
adds real write-time metadata only at the ES boundary.

Run from repo root:

    python3 agents/scripts/upsert_eval_memory_pattern.py \
      --memory agents/evals/results/memory-loop-ab-v2-2026-05-30/memory.json

    python3 agents/scripts/upsert_eval_memory_pattern.py \
      --memory agents/evals/results/memory-loop-ab-v2-2026-05-30/memory.json \
      --apply
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Any

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _AGENTS_ROOT.parent
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

DEFAULT_RECORD_SOURCE = "memory_loop_v2_eval"
DRIFT_PATTERNS_INDEX = "drift_patterns"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected JSON object")
    return value


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _build_doc(memory: dict[str, Any], record_source: str, timestamp: str) -> tuple[str, dict[str, Any]]:
    pattern = memory.get("pattern")
    if not isinstance(pattern, dict):
        raise SystemExit("memory.json must contain pattern object")

    pattern_id = pattern.get("pattern_id")
    if not isinstance(pattern_id, str) or not pattern_id.strip():
        raise SystemExit("pattern.pattern_id must be a non-empty string")

    description = pattern.get("pattern_description")
    if not isinstance(description, str) or not description.strip():
        raise SystemExit("pattern.pattern_description must be a non-empty string")

    pattern_type = pattern.get("pattern_type")
    if not isinstance(pattern_type, str) or not pattern_type.strip():
        raise SystemExit("pattern.pattern_type must be a non-empty string")

    source_event_ids = [str(item) for item in _as_list(pattern.get("source_event_ids")) if str(item).strip()]
    if not source_event_ids:
        raise SystemExit("pattern.source_event_ids must contain at least one id")

    domain_tags = [str(item) for item in _as_list(pattern.get("domain_tags")) if str(item).strip()]
    if not domain_tags:
        raise SystemExit("pattern.domain_tags must contain at least one tag")

    support_count = int(pattern.get("support_count", 0))
    if support_count != len(source_event_ids):
        support_count = len(source_event_ids)

    doc = {
        "record_source": record_source,
        "pattern_id": pattern_id,
        "pattern_description": description.strip(),
        "pattern_type": pattern_type,
        "domain_tags": domain_tags,
        "source_event_ids": source_event_ids,
        "support_count": support_count,
        "created_at": timestamp,
        "last_updated_at": timestamp,
    }
    return pattern_id, doc


def main() -> int:
    parser = argparse.ArgumentParser(description="Upsert a normalized eval memory pattern into Elasticsearch.")
    parser.add_argument("--memory", required=True, help="Path to normalized memory.json.")
    parser.add_argument("--record-source", default=DEFAULT_RECORD_SOURCE)
    parser.add_argument("--apply", action="store_true", help="Actually write to Elasticsearch. Default is dry-run.")
    args = parser.parse_args()

    memory = _load_json(Path(args.memory))
    pattern_id, doc = _build_doc(memory, args.record_source, _now())

    print(f"Prepared drift_patterns document _id={pattern_id}:")
    print(json.dumps(doc, indent=2))

    if not args.apply:
        print()
        print("Dry run. Pass --apply to upsert this document into Elasticsearch.")
        return 0

    client = ElasticsearchHttpClient()
    response = client.request("PUT", f"/{DRIFT_PATTERNS_INDEX}/_doc/{pattern_id}?refresh=true", doc)
    result = response.get("result")
    print()
    print(f"Upserted into {DRIFT_PATTERNS_INDEX}: result={result}")

    verify = client.request("GET", f"/{DRIFT_PATTERNS_INDEX}/_doc/{pattern_id}")
    source = verify.get("_source", {})
    print("Verified document:")
    print(json.dumps(source, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
