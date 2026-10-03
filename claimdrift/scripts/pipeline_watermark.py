"""Rewind the dispatch workflow's watermark (dispatch_state/_doc/main_flow) to just before the oldest eligible pair that
has no drift event yet. Called by `deploy/pipeline.sh batch N` before it re-enables the workflow: pairs the dispatcher
deferred while its budget was used up (answered 200 "paused") are then picked up again; pairs already analysed are
skipped by the dispatcher's idempotency check.

  uv run python claimdrift/scripts/pipeline_watermark.py [--apply]
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from claimdrift import es, store  # noqa: E402

# same filter as elastic/agent_builder/workflows/dispatch_new_pairs.template.yaml (search_new_pairs)
ELIGIBLE = {"bool": {"filter": [{"exists": {"field": "published_doi"}}, {"term": {"is_final_preprint": True}}],
                     "must_not": [{"term": {"record_source": "demo_seed"}}, {"term": {"version": "published"}}]}}


def oldest_pending() -> dict | None:
    """Oldest eligible pair (by ingested_at) without a drift event."""
    search_after = None
    while True:
        body = {"size": 500, "query": ELIGIBLE, "sort": [{"ingested_at": "asc"}, {"doi": "asc"}],
                "_source": ["doi", "published_doi", "ingested_at"]}
        if search_after:
            body["search_after"] = search_after
        hits = es.request("POST", "preprints/_search", body)["hits"]["hits"]
        if not hits:
            return None
        pairs = [h["_source"] for h in hits]
        done = {(e["preprint_doi"], e["published_doi"]) for e in es.hits(
            "drift_events", {"terms": {"preprint_doi": sorted({p["doi"].lower() for p in pairs})}}, size=1000,
            source_fields=["preprint_doi", "published_doi"])}
        for p in pairs:
            if (p["doi"].lower(), (p["published_doi"] or "").lower()) not in done:
                return p
        search_after = hits[-1]["sort"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    cur = (es.source("dispatch_state", "main_flow") or {}).get("last_seen_ingested_at")
    p = oldest_pending()
    if p is None:
        print(json.dumps({"watermark": cur, "oldest_pending": None, "changed": False}))
        return
    t = dt.datetime.fromisoformat(p["ingested_at"].replace("Z", "+00:00")) - dt.timedelta(seconds=1)
    new = t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    changed = cur is None or new < cur
    if a.apply and changed:
        es.put("dispatch_state", "main_flow", {"flow_name": "main_flow", "last_seen_ingested_at": new, "last_updated_at": store.now()})
    print(json.dumps({"watermark_before": cur, "watermark": new if changed else cur, "oldest_pending": p["doi"], "changed": changed}))


if __name__ == "__main__":
    main()
