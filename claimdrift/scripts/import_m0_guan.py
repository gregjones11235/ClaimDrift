"""Replace the library's guan_nejm_clinical event with the M0 experiment output (claims route), which contains a real
model quoting error: "(Fig. 2, see Table E1 in Supplementary Appendix)" where the preprint says "Table E2". The output
goes through the production pipeline (abstract-level severity, 事后校验 via MCP verify_quote, review triggers), so the
event lands in the review queue with quote_unverified -- a real case for the reviewer's quote correction (Check
verbatim). The previous (stuffed-route) event is backed up first. Citation runs are not queued.

Usage: uv run python claimdrift/scripts/import_m0_guan.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from claimdrift import config, drift_analyzer, pipeline, store  # noqa: E402
from claimdrift.documents import load_pair  # noqa: E402
from claimdrift.mcp_client import McpClient  # noqa: E402

CASE = "guan_nejm_clinical"
SRC = config.EXPERIMENTS_DIR / "results" / "mapreduce__M0.json"
BACKUP = config.EXPERIMENTS_DIR / "results" / "backup_guan_event_stuffed_2026-10-02.json"


def main():
    pair = load_pair(CASE)
    old = store.get_event(store.event_id_for(pair.preprint_doi, pair.published_doi))
    if old and not BACKUP.exists():
        BACKUP.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
        print("backed up", BACKUP.name)
    res = json.loads(SRC.read_text(encoding="utf-8"))["cases"][CASE]
    analysis = {"output": drift_analyzer.normalize(json.loads(json.dumps(res["output"]))),
                "route": {"route": "claims", "est_tokens": pair.est_tokens(), "limit": config.STUFF_LIMIT_TOKENS,
                          "forced": True, "prompt_version": "v4a"},
                "claims": {}, "raw_tail": "",
                "usage": {"drift_analyzer": {"source": f"imported from {SRC.name}", "model": config.MODEL_PRO}},
                "secs": res.get("secs_total")}
    with McpClient() as client:
        ev = pipeline.analyze_pair(pair, mcp_client=client, enqueue_citations=False, analysis=analysis)
    bad = [p for p in ev["provenance"] if not p["verified"]]
    print(f"{CASE}: diffs={len(ev['claim_diffs'])} quotes={ev['verification']['quotes_verified']}/{ev['verification']['quotes_total']} "
          f"review={ev['review_status']} {ev['review_reasons']}")
    for p in bad:
        print("  unverified:", p["claim_diff_idx"], p["doc_id"], p["quote"][-90:])


if __name__ == "__main__":
    main()
