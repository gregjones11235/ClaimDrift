"""One-off feasibility probe: can a semantic_text field copy_to a plain text
field, so the lexical (BM25) half of the hybrid is populated at index time with
ZERO write-code changes?

Context: pattern_description is semantic_text (ELSER only); the RRF "BM25"
sub-retriever currently auto-routes through ELSER, so hybrid is really
ELSER-self-fusion. To restore a real lexical signal we need an analyzed `text`
representation of the description. Two ways to fill it:
  (A) copy_to: declare pattern_description with `copy_to: pattern_description_text`
      and let ES populate the text field automatically. No write-code change.
  (B) explicit dual-write in elastic_write.py.

This probe tests whether (A) works on this serverless cluster by building a
THROWAWAY index `_copyto_probe_tmp`, indexing one doc that only sets the
semantic_text field, and checking the text field became lexically searchable.

SAFE: operates only on a temp index it creates and deletes. Touches no real data.
Run from agents/:
    uv run python scripts/probe_copyto_feasibility.py
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

TMP = "copyto-probe-tmp"
RARE = "zzqxphloxinated"  # a nonsense token guaranteed absent elsewhere; lexical-only match


def main() -> None:
    client = ElasticsearchHttpClient()

    # clean any stale temp index
    try:
        client.request("DELETE", f"/{TMP}")
    except Exception:  # noqa: BLE001
        pass

    mapping = {
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "pattern_id": {"type": "keyword"},
                "pattern_description": {
                    "type": "semantic_text",
                    "inference_id": ".elser-2-elastic",
                    "copy_to": "pattern_description_text",
                },
                "pattern_description_text": {"type": "text"},
            },
        }
    }

    try:
        try:
            client.put_index(TMP, mapping)
            print(f"[1] created temp index '{TMP}' with copy_to semantic_text -> text")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "copy_to" in msg or "semantic_text" in msg:
                print(f"[1] FAIL: cluster rejected copy_to ON semantic_text: {exc}")
                print("    -> copy_to not allowed on semantic_text; use explicit dual-write (option B).")
            else:
                print(f"[1] ERROR creating temp index (NOT a copy_to verdict): {exc}")
            return

        doc = {
            "pattern_id": "probe-1",
            "pattern_description": f"A drift pattern about {RARE} endpoint switching in trials.",
        }
        client.request("PUT", f"/{TMP}/_doc/probe-1?refresh=wait_for", doc)
        print("[2] indexed one doc setting ONLY pattern_description")
        time.sleep(1.0)

        # lexical-only probe: match the rare token on the text field. If copy_to
        # worked, the token is in the analyzed text field and this returns 1 hit.
        resp = client.request(
            "POST", f"/{TMP}/_search",
            {"query": {"match": {"pattern_description_text": RARE}}, "size": 1},
        )
        n = (resp.get("hits") or {}).get("total", {}).get("value", 0)
        if n >= 1:
            print(f"[3] PASS: rare token found via pattern_description_text "
                  f"(copy_to populated the lexical field automatically).")
            print("    -> Option A viable: mapping copy_to + RRF BM25 on the text field,")
            print("       ZERO write-code change. Reindex re-runs copy_to automatically.")
        else:
            print("[3] copy_to did not populate the text field (0 lexical hits).")
            print("    -> Fall back to explicit dual-write (option B).")
    finally:
        try:
            client.request("DELETE", f"/{TMP}")
            print(f"[cleanup] deleted temp index '{TMP}'.")
        except Exception as exc:  # noqa: BLE001
            print(f"[cleanup] WARN: could not delete '{TMP}': {exc}")


if __name__ == "__main__":
    main()
