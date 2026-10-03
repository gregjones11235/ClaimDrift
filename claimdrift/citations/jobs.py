"""Layer ② as asynchronous jobs (§3.1, P0.5 / P1.7). The `citation_runs` index is both the queue and the record.

  enqueue(target)        -> run doc, status "queued"           (called by the main pipeline after the event is written)
  work(once=False)       claims queued runs one at a time (optimistic concurrency on _seq_no) and runs the orchestra
                         step by step (runner.py): on the citation_finder engine when CLAIMDRIFT_ENGINES is set, else
                         in-process. The state is saved in the run doc after every step; a run whose job died
                         (status running, no step for STALE_MINUTES) is claimed again and resumes from its last state
  rerun_due(days=7)      layer ②': for every target whose last finished run is older than `days`, enqueue an incremental
                         run that only screens citing papers first published since then and skips papers already judged

A run writes one affected_citations doc per judged paper (all classes, so negatives stay auditable), the run's coverage
fields, and the event's citation_analysis summary. Human decisions on existing citation docs are never overwritten.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import socket
import time
import traceback
import uuid

from .. import config, es, review, store

log = logging.getLogger("claimdrift.citations")
RUNS, AC, EV = config.INDICES["citation_runs"], config.INDICES["affected_citations"], config.INDICES["drift_events"]
STALE_MINUTES = 30  # a running run without a saved step for this long belongs to a job that died


def enqueue(target: dict, kind: str = "initial", since: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    es.put(RUNS, run_id, {"run_id": run_id, "drift_event_id": target["drift_event_id"], "target_id": target["target_id"],
                          "paper_id": target.get("paper_id"), "kind": kind, "status": "queued", "target": target,
                          "terms": target.get("terms"), "since": since, "queued_at": store.now()})
    return run_id


def _claim_next() -> dict | None:
    stale = f"now-{STALE_MINUTES}m"
    q = {"bool": {"should": [
        {"term": {"status": "queued"}},
        {"bool": {"filter": [{"term": {"status": "running"}}, {"range": {"step_at": {"lt": stale}}}]}},
        {"bool": {"filter": [{"term": {"status": "running"}}, {"range": {"started_at": {"lt": stale}}}],
                  "must_not": [{"exists": {"field": "step_at"}}]}}], "minimum_should_match": 1}}
    r = es.search(RUNS, {"size": 5, "seq_no_primary_term": True, "query": q, "sort": [{"queued_at": "asc"}]})
    for h in r["hits"]["hits"]:
        doc = h["_source"] | {"status": "running", "worker": f"{socket.gethostname()}:{os.getpid()}"}
        if h["_source"].get("status") == "running":
            log.warning("resuming stale citation run %s from step %s", h["_id"], h["_source"].get("steps"))
            doc["step_at"] = store.now()  # keep other workers off it while this one resumes
        else:
            doc["started_at"] = store.now()
        try:
            es.put(RUNS, h["_id"], doc, if_seq_no=h["_seq_no"], if_primary_term=h["_primary_term"])
            return doc
        except es.ESError as e:
            if e.status == 409:  # another worker took it
                continue
            raise
    return None


def _judged_work_ids(event_id: str) -> set[str]:
    return {h["work_id"] for h in es.hits(AC, {"term": {"drift_event_id": event_id}}, size=10000, source_fields=["work_id"]) if h.get("work_id")}


def _save_state(run: dict, state: dict) -> None:
    from .runner import progress
    run["state"], run["steps"], run["step_at"], run["progress"] = state, (run.get("steps") or 0) + 1, store.now(), progress(state)
    es.update(RUNS, run["run_id"], {k: run[k] for k in ("state", "steps", "step_at", "progress")})


def run_job(run: dict, tools_via: str = "mcp") -> dict:
    from .. import engines
    target = run["target"]
    event_id = run["drift_event_id"]
    started = (run.get("started_at") or store.now())[:10]
    if engines.configured():
        agents, extra = engines.agents(), {}
    else:  # in-process; the handler uses the process-wide MCP client (or local tools)
        agents, extra = engines.LocalAgents(), {"tools": tools_via}
    state = run.get("state")
    if state is None:
        exclude = sorted(_judged_work_ids(event_id)) if run.get("kind") == "incremental" else []
        state = agents.call("citation_finder", {"op": "start", "target": target, "since": run.get("since"),
                                                "exclude": exclude} | extra)["state"]
        _save_state(run, state)
    while state["phase"] != "done":
        state = agents.call("citation_finder", {"op": "step", "state": state} | extra)["state"]
        _save_state(run, state)
        log.info("citation run %s: step %d -> %s", run["run_id"], run["steps"], state["phase"])
    res = state["result"]
    rows, n_sup = [], 0
    for w in res["citing_works"]:
        status, reasons = review.citation_status(w["cites"])
        ac_id, doc = store.affected_citation_doc(event_id, target, run["run_id"], w, status, reasons)
        old = es.source(AC, ac_id)
        if old and old.get("review_status") in ("approved", "rejected"):
            continue  # a human already decided on this paper
        rows.append((ac_id, doc))
        n_sup += w["cites"] == "superseded"
    es.bulk_index(AC, rows)
    # Notify straight away (no review gate; everything goes to the test inbox). A failed send never fails the run.
    try:
        from .. import notifier
        notifier.notify_pending(event_id=event_id)
    except Exception:  # noqa: BLE001
        log.exception("notification step failed for event %s", event_id)
    counts: dict[str, int] = {}
    for w in res["citing_works"]:
        counts[w["cites"]] = counts.get(w["cites"], 0) + 1
    finished = {k: res.get(k) for k in ("n_candidates", "n_judged", "n_unjudged", "coverage_complete", "coverage",
                                        "coverage_notes", "unjudged", "judged_by", "calls", "tokens", "events", "prefetch", "summary")}
    es.put(RUNS, run["run_id"], run | finished | {"status": "done", "finished_at": store.now(), "checked_until": started,
                                                 "counts": counts, "terms": res["terms"], "state": None,
                                                 "progress": {"phase": "done", "line": "done"}})
    _update_event(event_id, run["run_id"], started)
    log.info("citation run %s (%s) done: %s", run["run_id"], target["target_id"], counts)
    return res


def _update_event(event_id: str, run_id: str, checked_until: str | None) -> None:
    ev = store.get_event(event_id)
    if not ev:
        return
    runs = es.hits(RUNS, {"term": {"drift_event_id": event_id}}, size=200, source_fields=["status", "coverage_complete", "run_id"])
    acs = es.hits(AC, {"term": {"drift_event_id": event_id}}, size=10000, source_fields=["cites", "needs_notification"])
    ca = ev.get("citation_analysis") or {}
    statuses = {r.get("status") for r in runs}
    ca.update({"status": "running" if statuses & {"queued", "running"} else ("failed" if "failed" in statuses else "done"),
               "run_ids": sorted({*(ca.get("run_ids") or []), run_id}), "checked_until": checked_until or ca.get("checked_until"),
               "n_superseded": sum(a.get("cites") == "superseded" for a in acs),
               "coverage_complete": all(r.get("coverage_complete") for r in runs if r.get("status") == "done"),
               "updated_at": store.now()})
    es.update(EV, event_id, {"citation_analysis": ca})


def work(once: bool = False, tools_via: str = "mcp", poll_secs: float = 10.0) -> int:
    """Process queued runs. once=True: drain the queue and return; else poll forever."""
    n = 0
    while True:
        run = _claim_next()
        if run is None:
            if once:
                return n
            time.sleep(poll_secs)
            continue
        try:
            run_job(run, tools_via=tools_via)
        except Exception as e:  # noqa: BLE001 -- the last saved state stays in the doc for inspection
            log.exception("citation run %s failed", run["run_id"])
            es.put(RUNS, run["run_id"], run | {"status": "failed", "finished_at": store.now(),
                                               "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}"})
            _update_event(run["drift_event_id"], run["run_id"], None)
        n += 1


def rerun_due(older_than_days: int = 7) -> list[str]:
    """Enqueue incremental runs for targets whose newest finished run is older than the threshold."""
    cutoff = (dt.date.today() - dt.timedelta(days=older_than_days)).isoformat()
    latest: dict[str, dict] = {}
    for r in es.hits(RUNS, {"terms": {"status": ["done", "queued", "running"]}}, size=10000, sort=[{"queued_at": "desc"}]):
        latest.setdefault(r["target_id"], r)
    queued = []
    for tid, r in latest.items():
        if r["status"] != "done" or not r.get("checked_until") or r["checked_until"] > cutoff:
            continue
        queued.append(enqueue(r["target"], kind="incremental", since=r["checked_until"]))
    return queued
