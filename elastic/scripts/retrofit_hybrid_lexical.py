"""Retrofit the hybrid lexical field onto the live `drift_patterns` PRIMARY
index in-place — no blue-green rebuild, no shadow index, no steady-state change.

Why in-place (not rebuild): the `drift_patterns_v2` shadow index + alias swap is
the C1 mechanism for the RARE, human-triggered curator backfill (design B.1,
contracts.md §2.2.5). Using it just to deploy a mapping tweak wrongly parks the
read alias on a non-steady-state index and splits writes (memory_synthesizer
writes `drift_patterns`) from reads (alias -> v2). The correct steady state is
alias -> `drift_patterns`, reads == writes. This script lands the hybrid fix
THERE, leaving the curator/shadow mechanism untouched for its real purpose.

What it does (verified viable by scripts/probe_hotadd_lexical.py):
  1. PUT _mapping: add `pattern_description_text` (text) AND add
     `copy_to: pattern_description_text` onto the existing semantic_text
     `pattern_description` field (ES accepts this on serverless 9.5).
  2. _update_by_query (no script): re-runs copy_to over every existing doc,
     backfilling the lexical mirror.
  3. Verify every doc has a non-empty lexical field.

Dry-run by default; pass --apply to write. Run from agents/:
    uv run python ../elastic/scripts/retrofit_hybrid_lexical.py
    uv run python ../elastic/scripts/retrofit_hybrid_lexical.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "agents"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / "agents" / ".env")

from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

PRIMARY_INDEX = "drift_patterns"
LEXICAL_FIELD = "pattern_description_text"
SEMANTIC_FIELD = "pattern_description"
INFERENCE_ID = ".elser-2-elastic"


def _count(client, index, body=None):
    return int(client.request("POST", f"/{index}/_count", body or {}).get("count", 0))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="Actually write. Default dry-run.")
    args = ap.parse_args()

    client = ElasticsearchHttpClient()

    total = _count(client, PRIMARY_INDEX)
    already = _count(client, PRIMARY_INDEX, {"query": {"exists": {"field": LEXICAL_FIELD}}})
    print(f"index '{PRIMARY_INDEX}': {total} docs, {already} already have '{LEXICAL_FIELD}'")

    mapping_patch = {
        "properties": {
            LEXICAL_FIELD: {"type": "text"},
            SEMANTIC_FIELD: {
                "type": "semantic_text",
                "inference_id": INFERENCE_ID,
                "copy_to": LEXICAL_FIELD,
            },
        }
    }

    if not args.apply:
        print("\nDry run. Would:")
        print(f"  1. PUT /{PRIMARY_INDEX}/_mapping  (add {LEXICAL_FIELD}: text + copy_to on {SEMANTIC_FIELD})")
        print(f"     body: {json.dumps(mapping_patch)}")
        print(f"  2. POST /{PRIMARY_INDEX}/_update_by_query?refresh=true  (backfill {total} docs via copy_to)")
        print(f"  3. verify all {total} docs have non-empty {LEXICAL_FIELD}")
        print("\nPass --apply to execute.")
        return 0

    # 1. mapping
    client.request("PUT", f"/{PRIMARY_INDEX}/_mapping", mapping_patch)
    print(f"[1/3] mapping patched: {LEXICAL_FIELD} added + copy_to on {SEMANTIC_FIELD}.")

    # 2. backfill — plain update_by_query re-runs copy_to on every doc.
    resp = client.request(
        "POST", f"/{PRIMARY_INDEX}/_update_by_query?refresh=true&wait_for_completion=true", {}
    )
    failures = resp.get("failures") or []
    if failures:
        print(f"[2/3] _update_by_query reported {len(failures)} failure(s):", file=sys.stderr)
        for f in failures[:5]:
            print(f"      {json.dumps(f)[:300]}", file=sys.stderr)
        return 1
    print(f"[2/3] backfilled {resp.get('updated')} doc(s) via copy_to.")

    # 3. verify
    time.sleep(1.0)
    have = _count(client, PRIMARY_INDEX, {"query": {"exists": {"field": LEXICAL_FIELD}}})
    print(f"[3/3] {have}/{total} docs now have a non-empty {LEXICAL_FIELD}.")
    if have != total:
        print("WARNING: not all docs backfilled. Investigate before relying on hybrid.", file=sys.stderr)
        return 1
    print("\nDONE: drift_patterns is now hybrid in-place. Reads (alias) and writes "
          "both target this index. Run scripts/verify_hybrid_fix.py to confirm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
