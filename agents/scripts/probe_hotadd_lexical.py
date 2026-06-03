"""One-off: can we add a lexical mirror to an EXISTING semantic_text field
in-place (no blue-green reindex), so the hybrid fix lands on the live
`drift_patterns` primary index without disturbing its steady-state role?

Two things must both work on a throwaway index that mimics drift_patterns
(semantic_text already populated, docs already written):
  A) PUT mapping to ADD `copy_to: pattern_description_text` to the EXISTING
     `pattern_description` field  ->  ES usually REJECTS changing an existing
     field's mapping. If rejected, copy_to-on-existing is not an option.
  B) Fallback: add a NEW independent `pattern_description_text` text field, then
     backfill existing docs into it with _update_by_query + a painless script
     that copies pattern_description's text. This always works and is in-place.

This probe builds `hotadd-probe-tmp`, writes a doc the OLD way (no lexical
field), then tries (A); if (A) fails it proves (B). Cleans up after itself.

SAFE: throwaway index only. Run from agents/:
    uv run python scripts/probe_hotadd_lexical.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_AGENTS_ROOT, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_AGENTS_ROOT / ".env")

from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

TMP = "hotadd-probe-tmp"
RARE = "zzqxphloxinated"


def main() -> None:
    client = ElasticsearchHttpClient()
    try:
        client.request("DELETE", f"/{TMP}")
    except Exception:  # noqa: BLE001
        pass

    # 1. build an index like the CURRENT drift_patterns: semantic_text, NO mirror.
    base = {
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "pattern_id": {"type": "keyword"},
                "pattern_description": {"type": "semantic_text", "inference_id": ".elser-2-elastic"},
            },
        }
    }
    client.put_index(TMP, base)
    client.request("PUT", f"/{TMP}/_doc/d1?refresh=wait_for", {
        "pattern_id": "d1",
        "pattern_description": f"Outcome switching pattern mentioning {RARE} in trials.",
    })
    print(f"[setup] built '{TMP}' (semantic_text only) + 1 doc the old way.")

    # 2. add the lexical text field (always allowed: it's a NEW field).
    add_field = {"properties": {"pattern_description_text": {"type": "text"}}}
    client.request("PUT", f"/{TMP}/_mapping", add_field)
    print("[A1] added NEW field pattern_description_text via PUT _mapping: OK")

    # 3a. try to ALSO put copy_to on the EXISTING semantic_text field.
    copyto_ok = False
    try:
        client.request("PUT", f"/{TMP}/_mapping", {
            "properties": {
                "pattern_description": {
                    "type": "semantic_text",
                    "inference_id": ".elser-2-elastic",
                    "copy_to": "pattern_description_text",
                }
            }
        })
        copyto_ok = True
        print("[A2] PUT copy_to onto EXISTING pattern_description: ACCEPTED")
    except Exception as exc:  # noqa: BLE001
        print(f"[A2] PUT copy_to onto EXISTING field REJECTED (expected): {str(exc)[:160]}")

    # 4. backfill existing docs into the lexical field.
    #    If copy_to was accepted, _update_by_query re-runs copy_to.
    #    If not, use an explicit painless copy of the description text.
    if copyto_ok:
        body = {}  # no-op reindex; copy_to runs on each doc
        print("[B] backfilling via plain _update_by_query (copy_to re-runs)...")
    else:
        body = {
            "script": {
                "source": (
                    "def d = ctx._source.pattern_description; "
                    "if (d instanceof Map) { d = d.text != null ? d.text : ''; } "
                    "ctx._source.pattern_description_text = d;"
                ),
                "lang": "painless",
            }
        }
        print("[B] backfilling via _update_by_query + painless text copy...")
    try:
        resp = client.request("POST", f"/{TMP}/_update_by_query?refresh=true&wait_for_completion=true", body)
        print(f"    updated {resp.get('updated')} doc(s), failures={len(resp.get('failures') or [])}")
    except Exception as exc:  # noqa: BLE001
        print(f"    _update_by_query ERROR: {str(exc)[:200]}")

    # 5. verify lexical search now works on the backfilled field.
    time.sleep(1.0)
    resp = client.request("POST", f"/{TMP}/_search",
                          {"query": {"match": {"pattern_description_text": RARE}}, "size": 1})
    n = (resp.get("hits") or {}).get("total", {}).get("value", 0)
    print(f"\n[verify] lexical match on backfilled field: {n} hit(s)")
    if n >= 1:
        path = "copy_to-on-existing + update_by_query" if copyto_ok else "new-field + painless backfill"
        print(f"PASS: in-place hybrid retrofit works via: {path}")
        print("      -> the live drift_patterns can get hybrid WITHOUT a blue-green rebuild.")
    else:
        print("FAIL: backfill did not populate the lexical field; in-place retrofit not viable.")

    try:
        client.request("DELETE", f"/{TMP}")
        print(f"\n[cleanup] deleted '{TMP}'.")
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] WARN: {exc}")


if __name__ == "__main__":
    main()
