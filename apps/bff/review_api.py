"""BFF routes for the new system: human review queue (P1.8) and author self-check (P1.10).

Backed by the `claimdrift` package against the local Elasticsearch (CLAIMDRIFT_ES). Routes:

  GET  /api/review-queue?kind=all|events|citations&status=pending
  GET  /api/review/events/<event_id>          event + provenance + citation runs + its citations
  GET  /api/review/citations/<ac_id>          citation + the drift change it concerns
  POST /api/review/events/<event_id>          {"decision": "approved|rejected", "reviewer": "<optional>", "note": "...",
                                               "corrected": {"abstract_class", "fulltext_tier",
                                                             "claim_diffs": [{"idx", "root_cause", "severity_tier"}]}}
  POST /api/review/citations/<ac_id>          same; "corrected": {"cites": "...", "role": "..."}
  GET  /api/drift-events/<event_id>/citation-runs
  POST /api/selfcheck/published              {"paper": "<DOI | PMCID | PPR id>"}  path 3, through the MCP tools
  POST /api/selfcheck/sentences               {"sentences": ["..."], "mode": "hybrid|elser|bm25"}
  POST /api/selfcheck/references              {"text": "<reference list>"}
  POST /api/selfcheck/analyze                 {"preprint_doi": "10.1101/..."}   on-demand layer ① for a missing preprint
  GET  /api/selfcheck/analyze?doi=...         status of that on-demand analysis

Review decisions only change review fields (and, for citations, an optional corrected verdict); sending is a separate
step (`python -m claimdrift notify`) that re-checks the gate.
"""
from __future__ import annotations

import threading
from typing import Any

from claimdrift import config, es, review, selfcheck

EV, AC, RUNS = config.INDICES["drift_events"], config.INDICES["affected_citations"], config.INDICES["citation_runs"]
REVIEW_STATUSES = {"pending", "approved", "rejected", "not_required"}

_ondemand_lock = threading.Lock()


def mcp_client():
    """One shared MCP client per BFF process: stdio locally, the deployed tool service when CLAIMDRIFT_MCP_URL is set."""
    from claimdrift.mcp_client import shared
    return shared()


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str = ""):
        super().__init__(message or error)
        self.status, self.error, self.message = status, error, message


def _event_row(ev: dict) -> dict:
    return {"kind": "event", "id": ev["event_id"], "event_id": ev["event_id"], "paper_id": ev.get("paper_id"),
            "preprint_doi": ev.get("preprint_doi"), "published_doi": ev.get("published_doi"), "title": ev.get("preprint_title"),
            "review_status": ev.get("review_status"), "review_reasons": ev.get("review_reasons") or [],
            "fulltext_tier": (ev.get("fulltext_severity") or {}).get("tier"),
            "abstract_class": (ev.get("abstract_severity") or {}).get("class"),
            "verification": ev.get("verification"), "detected_at": ev.get("detected_at"),
            "reviewer": ev.get("reviewer"), "reviewed_at": ev.get("reviewed_at")}


def _citation_row(ac: dict) -> dict:
    return {"kind": "citation", "id": ac["affected_citation_id"], "drift_event_id": ac.get("drift_event_id"),
            "work_id": ac.get("work_id"), "title": ac.get("citing_paper_title"), "cites": ac.get("cites"), "role": ac.get("role"),
            "notify_priority": ac.get("notify_priority"), "sentence": ac.get("sentence"), "sentence_verified": ac.get("sentence_verified"),
            "flags": ac.get("flags") or [], "found_via": ac.get("found_via"), "review_status": ac.get("review_status"),
            "review_reasons": ac.get("review_reasons") or [], "reviewer": ac.get("reviewer"), "reviewed_at": ac.get("reviewed_at")}


def review_queue(kind: str = "all", status: str = "pending") -> dict:
    if status not in REVIEW_STATUSES:
        raise ApiError(400, "bad_status", f"status must be one of {sorted(REVIEW_STATUSES)}")
    q = {"term": {"review_status": status}}
    out: dict[str, Any] = {"status": status}
    if kind in ("all", "events"):
        rows = es.hits(EV, q, size=500, sort=[{"detected_at": {"order": "desc", "unmapped_type": "date"}}],
                       source_fields={"excludes": ["usage", "claim_diffs", "provenance", "tool_trace"]})
        out["events"] = [_event_row(r) for r in rows]
    if kind in ("all", "citations"):
        # high-priority notifications first (model inputs), then everything else
        rows = es.hits(AC, q, size=2000, sort=[{"notify_priority": {"order": "asc", "unmapped_type": "keyword"}},
                                               {"scored_at": {"order": "desc", "unmapped_type": "date"}}])
        out["citations"] = [_citation_row(r) for r in rows]
    return out


def review_event_detail(event_id: str) -> dict:
    ev = es.source(EV, event_id)
    if ev is None:
        raise ApiError(404, "drift_event_not_found")
    runs = es.hits(RUNS, {"term": {"drift_event_id": event_id}}, size=50, sort=[{"queued_at": "desc"}],
                   source_fields={"excludes": ["events", "target", "state"]})  # state can be large; progress is the live view
    cites = es.hits(AC, {"term": {"drift_event_id": event_id}}, size=2000)
    ev.pop("usage", None)
    return {"event": ev, "citation_runs": runs, "citations": [_citation_row(c) for c in cites]}


def review_citation_detail(ac_id: str) -> dict:
    ac = es.source(AC, ac_id)
    if ac is None:
        raise ApiError(404, "affected_citation_not_found")
    ev = es.source(EV, ac["drift_event_id"]) or {}
    idx = ac.get("claim_diff_idx")
    diff = ev.get("claim_diffs", [])[idx] if isinstance(idx, int) and 0 <= idx < len(ev.get("claim_diffs") or []) else None
    return {"citation": ac, "event": _event_row(ev) if ev else None, "claim_diff": diff,
            "drift_summary": ev.get("drift_summary")}


def decide(kind: str, doc_id: str, body: dict) -> dict:
    index = {"events": EV, "citations": AC}.get(kind)
    if index is None:
        raise ApiError(404, "unknown_review_kind")
    doc = es.source(index, doc_id)
    if doc is None:
        raise ApiError(404, "not_found")
    corrected = body.get("corrected") or None
    if corrected is not None and not isinstance(corrected, dict):
        raise ApiError(400, "bad_correction", "corrected must be an object")
    try:
        review.decide(doc, body.get("decision"), body.get("reviewer"), body.get("note") or "", corrected,
                      kind="event" if kind == "events" else "citation")
    except ValueError as e:
        raise ApiError(400, "bad_decision", str(e)) from e
    es.put(index, doc_id, doc)
    if kind == "events":
        selfcheck.index_event(doc)  # rejected events leave the self-check index; approved ones (re)enter it
    out = {k: doc.get(k) for k in ("review_status", "reviewer", "reviewed_at", "review_note", "corrected_fields", "machine_verdict")}
    if kind == "citations":
        out |= {"cites": doc.get("cites"), "role": doc.get("role")}
    else:
        out |= {"abstract_class": (doc.get("abstract_severity") or {}).get("class"),
                "fulltext_tier": (doc.get("fulltext_severity") or {}).get("tier")}
    return out


def citation_runs(event_id: str) -> dict:
    rows = es.hits(RUNS, {"term": {"drift_event_id": event_id}}, size=50, sort=[{"queued_at": "desc"}],
                   source_fields={"excludes": ["events", "target", "state"]})  # state can be large; progress is the live view
    return {"items": rows, "count": len(rows)}


def selfcheck_published(body: dict) -> dict:
    ref = (body.get("paper") or "").strip()
    if not ref:
        raise ApiError(400, "no_paper")
    try:
        res = selfcheck.check_published(ref, mcp_client())
    except ValueError as e:
        raise ApiError(400, "bad_paper", str(e)) from e
    res["not_in_library_status"] = {r["doi"]: (selfcheck.ondemand_get(r["doi"]) or {}).get("status")
                                    for r in res.get("not_in_library") or []}
    return res


def selfcheck_sentences(body: dict) -> dict:
    sents = [s for s in body.get("sentences") or [] if isinstance(s, str) and s.strip()]
    if not sents:
        raise ApiError(400, "no_sentences")
    mode = body.get("mode") or "hybrid"
    if mode not in ("hybrid", "elser", "bm25"):
        raise ApiError(400, "bad_mode")
    return selfcheck.check_sentences(sents, mode=mode)


def selfcheck_references(body: dict) -> dict:
    text = body.get("text") or ""
    if not text.strip():
        raise ApiError(400, "no_text")
    res = selfcheck.check_references(text)
    res["not_in_library_status"] = {d: (selfcheck.ondemand_get(d) or {}).get("status") for d in res["not_in_library"]}
    return res


ONDEMAND_STEPS = 6  # fetch, extractor (preprint), extractor (published), analyzer, verification + save, queued


def _progress_line(msg: dict) -> tuple[str | None, bool]:
    """(log line, counts as a finished step) for one pipeline / supervisor frame; None for frames not worth a line."""
    f, d = msg.get("frame"), msg.get("data") or {}
    node = (d.get("node") or "").replace("_", " ")
    if f in ("pipeline.fetching", "pipeline.saving"):
        return d["summary"], False
    if f == "pipeline.ready":
        return d["summary"], True
    if f == "node.started":
        return f"{node}: {d.get('action')}", False
    if f == "node.done":
        return f"{node}: {d.get('summary')}", True
    if f == "node.error":
        return f"{node} failed: {d.get('message')}", False
    return None, False


def _run_ondemand(doi: str) -> None:
    """Runs in a background thread; the status doc is the user's live view (polled by the self-check page)."""
    import time
    from claimdrift import pipeline
    log: list[dict] = []
    done = {"n": 0}
    t0 = time.time()

    def on_progress(msg: dict) -> None:
        line, finished = _progress_line(msg)
        if line is None:
            return
        done["n"] += finished
        log.append({"t": round(time.time() - t0), "line": line[:300], "done": finished})
        try:
            selfcheck.ondemand_put(doi, {"status": "running", "progress": {
                "line": line[:300], "steps_done": done["n"], "steps_total": ONDEMAND_STEPS, "log": log[-20:]}})
        except Exception:  # noqa: BLE001 -- progress is display only
            pass

    try:
        ev = pipeline.analyze_dois(doi, on_progress=on_progress)
        queued = (ev.get("citation_analysis") or {}).get("status") == "queued"
        log.append({"t": round(time.time() - t0), "done": True, "line": "citation analysis queued (runs in the background; "
                    "progress on the event page)" if queued else "no traceable revised value: no citation analysis"})
        selfcheck.ondemand_put(doi, {"status": "done", "event_id": ev["event_id"], "progress": {
            "line": "done", "steps_done": ONDEMAND_STEPS, "steps_total": ONDEMAND_STEPS, "log": log[-20:]}})
    except Exception as e:  # noqa: BLE001
        selfcheck.ondemand_put(doi, {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500],
                                     "progress": {"line": "failed", "steps_done": done["n"], "steps_total": ONDEMAND_STEPS, "log": log[-20:]}})


def selfcheck_analyze(body: dict) -> dict:
    doi = (body.get("preprint_doi") or "").strip().lower()
    if not doi.startswith("10.1101/"):
        raise ApiError(400, "not_a_biorxiv_medrxiv_doi")
    with _ondemand_lock:
        cur = selfcheck.ondemand_get(doi)
        if cur and cur.get("status") in ("running", "done"):
            return {"preprint_doi": doi} | cur
        pre = selfcheck.precheck_doi(doi)
        if pre["status"] not in ("ready", "check_failed"):
            return {"preprint_doi": doi, "status": "not_analysable", "error": pre.get("detail"), "precheck": pre}
        selfcheck.ondemand_put(doi, {"status": "running"})
    threading.Thread(target=_run_ondemand, args=(doi,), daemon=True).start()
    return {"preprint_doi": doi, "status": "running"}


def selfcheck_analyze_status(doi: str) -> dict:
    doi = (doi or "").strip().lower()
    return {"preprint_doi": doi} | (selfcheck.ondemand_get(doi) or {"status": "unknown"})


