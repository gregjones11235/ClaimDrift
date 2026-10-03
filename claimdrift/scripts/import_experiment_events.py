"""Write the case-bank drift events into the library WITHOUT re-running drift_analyzer.

The experiment run results/fulltext_stuffed__gemini_gemini-3.1-pro-preview_pv4a.json used exactly the production prompt
(v4a, byte-identical) and model (gemini-3.1-pro-preview) on the stuffed route. Its outputs are passed through the
production pipeline (pipeline.analyze_pair(analysis=...)): abstract-level severity (one pro call per pair), 事后校验 via
MCP, review triggers, drift_events, self-check index. Citation runs are NOT queued. Events that already exist (from a
real pipeline run) are skipped unless --overwrite.

Usage: bash claimdrift/run.sh ... -> uv run python claimdrift/scripts/import_experiment_events.py [--overwrite]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from claimdrift import config, drift_analyzer, pipeline, store  # noqa: E402
from claimdrift.documents import load_pair  # noqa: E402
from claimdrift.mcp_client import McpClient  # noqa: E402

SRC = config.EXPERIMENTS_DIR / "results" / "fulltext_stuffed__gemini_gemini-3.1-pro-preview_pv4a.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()
    cases = json.loads(SRC.read_text(encoding="utf-8"))["cases"]
    done = skipped = 0
    with McpClient() as client:
        for case, res in cases.items():
            pair = load_pair(case)
            if not a.overwrite and store.get_event(store.event_id_for(pair.preprint_doi, pair.published_doi)):
                print(f"skip {case}: event exists", flush=True)
                skipped += 1
                continue
            analysis = {"output": drift_analyzer.normalize(json.loads(json.dumps(res["output"]))),
                        "route": {"route": "stuffed", "est_tokens": pair.est_tokens(), "limit": config.STUFF_LIMIT_TOKENS,
                                  "forced": False, "prompt_version": "v4a"},
                        "claims": {}, "raw_tail": "",
                        "usage": {"drift_analyzer": {"source": f"imported from {SRC.name}", "prompt_tokens": res.get("prompt_tokens"),
                                                     "output_tokens": res.get("output_tokens"), "model": config.MODEL_PRO}},
                        "secs": res.get("wall_secs")}
            ev = pipeline.analyze_pair(pair, mcp_client=client, enqueue_citations=False, analysis=analysis)
            done += 1
            print(f"{case}: diffs={len(ev['claim_diffs'])} fulltext={ev['fulltext_severity']['tier']} "
                  f"abstract={ev['abstract_severity'].get('class')} quotes={ev['verification']['quotes_verified']}/"
                  f"{ev['verification']['quotes_total']} review={ev['review_status']} {ev['review_reasons']}", flush=True)
    print(f"imported {done}, skipped {skipped}")


if __name__ == "__main__":
    main()
