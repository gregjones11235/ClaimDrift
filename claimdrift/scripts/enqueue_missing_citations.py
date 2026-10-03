"""Queue the citation analysis for drift events that have a traceable revised value but were never queued
(citation_analysis.status == "not_queued": events written by import_experiment_events.py / import_m0_guan.py / eval-drift,
which deliberately skip the queue). The citation-dispatch job then processes them like any other run.

  uv run python claimdrift/scripts/enqueue_missing_citations.py [--dry-run] [--exclude guan_nejm_clinical]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from claimdrift import config, es, store  # noqa: E402
from claimdrift.citations import jobs  # noqa: E402
from claimdrift.citations.targets import build_targets  # noqa: E402

EV = config.INDICES["drift_events"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--exclude", default="", help="comma-separated paper_ids to leave out")
    a = ap.parse_args()
    skip = {x for x in a.exclude.split(",") if x}
    out = []
    for ev in es.hits(EV, {"term": {"citation_analysis.status": "not_queued"}}, size=500):
        if ev.get("paper_id") in skip:
            continue
        targets = build_targets(ev, ev.get("paper_id") or "", ev.get("preprint_title") or "", ev.get("first_author") or "")
        row = {"paper_id": ev.get("paper_id"), "event_id": ev["event_id"], "targets": len(targets)}
        if targets and not a.dry_run:
            run_ids = [jobs.enqueue(t, kind="initial") for t in targets]
            ca = (ev.get("citation_analysis") or {}) | {"status": "queued", "n_targets": len(targets), "run_ids": run_ids,
                                                         "updated_at": store.now()}
            es.update(EV, ev["event_id"], {"citation_analysis": ca})
            row["run_ids"] = run_ids
        out.append(row)
    print(json.dumps({"events": len(out), "targets": sum(r["targets"] for r in out), "rows": out}, indent=1))


if __name__ == "__main__":
    main()
