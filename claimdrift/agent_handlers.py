"""The five agents as plain functions: handler(payload: dict, emit) -> result dict. emit(dict) reports progress.

Deployed, each handler runs inside its own Vertex AI Agent Engine engine (agents/<name>/agent.py wraps it in an ADK
BaseAgent); locally the same handlers run in-process. Either way the caller reaches them through engines.agents(), so
the deployed system and a local run execute the same code with the same prompts and models.

Design rule (部署方案 A7): agents only compute. No handler writes Elasticsearch, sends mail or writes a cache; the tool
service (MCP) is their only window on full texts and Europe PMC.

  claim_extractor  {"doc_id", "doc"}                                   -> {"claims", "usage"}             (flash)
  drift_analyzer   {"pair", "claims", "extract_usage", "force_route"}  -> {"analysis", "abstract_severity"} (pro)
  citation_finder  {"op": "start", "target", "since", "exclude"}
                 | {"op": "step", "state"}                             -> {"state"}                       (pro, MCP)
  notifier         {"ac", "event"}                                     -> {"subject", "body"}             (flash)
  supervisor       {"mode": "analyze"|"full", "pair", "force_route",
                    "max_targets", "max_alerts"}                       -> {"computed", ["citation", "drafts"]}

The supervisor is fixed orchestration code (no model of its own): claim_extractor x2 in parallel -> drift_analyzer ->
quote verification through MCP -> (mode "full", the Playground) citation_finder step by step per traceable value ->
notifier xN in parallel. Its progress messages are the Playground's SSE frames ({"frame": <event>, "data": {...}}).
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
from typing import Callable

from . import serial

log = logging.getLogger("claimdrift.agents")
Emit = Callable[[dict], None]


def _mcp():
    from .mcp_client import shared
    return shared()


def claim_extractor(payload: dict, emit: Emit) -> dict:
    from . import claim_extractor as ce
    doc = serial.doc_from_dict(payload["doc"])
    emit({"kind": "extract_start", "doc_id": payload["doc_id"], "chunks": len(ce.chunks(doc))})
    claims, usage = ce.extract(payload["doc_id"], doc)
    return {"claims": claims, "usage": usage}


def drift_analyzer(payload: dict, emit: Emit) -> dict:
    from . import abstract_severity, drift_analyzer as da, llm
    pair = serial.pair_from_dict(payload["pair"])
    emit({"kind": "analyze_start", "route": "claims" if payload.get("claims") is not None else payload.get("force_route")})
    with cf.ThreadPoolExecutor(2) as ex:  # the two pro calls are independent
        f_drift = ex.submit(da.analyze, pair, llm.pro(), payload.get("force_route"), claims=payload.get("claims"),
                            extract_usage=payload.get("extract_usage"))
        f_abs = ex.submit(abstract_severity.classify, llm.pro(), pair.preprint.abstract, pair.published.abstract)
        return {"analysis": f_drift.result(), "abstract_severity": f_abs.result()}


def citation_finder(payload: dict, emit: Emit) -> dict:
    from .citations import runner
    via = payload.get("tools", "mcp")
    client = _mcp() if via == "mcp" else None
    if payload.get("op") == "start":
        state = runner.start(payload["target"], payload.get("since"), payload.get("exclude") or (), via, client, on_event=emit)
    elif payload.get("op") == "step":
        state = runner.step(payload["state"], via, client, on_event=emit)
    else:
        raise ValueError(f"citation_finder: unknown op {payload.get('op')!r}")
    return {"state": state}


def notifier(payload: dict, emit: Emit) -> dict:
    from . import notifier as nt
    subject, body = nt.draft(payload["ac"], payload["event"])
    return {"subject": subject, "body": body}


def supervisor(payload: dict, emit: Emit, agents=None) -> dict:
    from . import engines, pipeline, store
    from . import claim_extractor as ce
    from . import notifier as nt
    from .citations.runner import describe_event, progress_line
    agents = agents or engines.agents()
    pair = serial.pair_from_dict(payload["pair"])
    force_route = payload.get("force_route")

    def frame(event: str, **data) -> None:
        emit({"frame": event, "data": data})

    # ---- claim_extractor x2 (parallel)
    claims = extract_usage = None
    if force_route != "stuffed":
        docs = [(0, pair.pre_id, pair.preprint, "preprint v1"), (1, "published", pair.published, "published version")]
        for lane, _, doc, name in docs:
            frame("node.started", node="claim_extractor", lane=lane, action=f"reading {name}")
            frame("node.active", node="claim_extractor", lane=lane, action=f"{len(ce.chunks(doc))} section chunk(s) of the {name}")
        claims, usages = {}, []
        with cf.ThreadPoolExecutor(2) as ex:
            futs = {ex.submit(agents.call, "claim_extractor", {"doc_id": doc_id, "doc": serial.doc_to_dict(doc)}): (lane, doc_id, name)
                    for lane, doc_id, doc, name in docs}
            for f in cf.as_completed(futs):
                lane, doc_id, name = futs[f]
                try:
                    r = f.result()
                except Exception as e:
                    frame("node.error", node="claim_extractor", lane=lane, message=f"{type(e).__name__}: {e}"[:300])
                    raise
                claims[doc_id] = r["claims"]
                usages.append(r["usage"])
                n_f = sum(c.get("kind") == "finding" for c in r["claims"])
                frame("node.done", node="claim_extractor", lane=lane,
                      summary=f"{len(r['claims'])} claims from the {name} ({n_f} findings, {len(r['claims']) - n_f} method/definition)")
        extract_usage = {k: sum(u.get(k, 0) for u in usages) for k in ("calls", "prompt_tokens", "output_tokens")} | {"model": usages[0].get("model")}

    # ---- drift_analyzer (+ abstract-level severity), then quote verification through MCP
    frame("node.started", node="drift_analyzer", lane=0, action="comparing the two claim lists (v4a)")
    try:
        r = agents.call("drift_analyzer", {"pair": payload["pair"], "claims": claims, "extract_usage": extract_usage,
                                           "force_route": force_route})
        computed = pipeline.compute(pair, mcp_client=_mcp(), analysis=r["analysis"], abs_sev=r["abstract_severity"])
    except Exception as e:
        frame("node.error", node="drift_analyzer", lane=0, message=f"{type(e).__name__}: {e}"[:300])
        raise
    event = computed["event"]
    ver, diffs = event.get("verification") or {}, event.get("claim_diffs") or []
    frame("drift.minted", event_id=event["event_id"], drift_summary=event.get("drift_summary"))
    frame("node.done", node="drift_analyzer", lane=0, summary=
          f"{len(diffs)} change(s) · full text {(event.get('fulltext_severity') or {}).get('tier')} · abstract "
          f"{(event.get('abstract_severity') or {}).get('class')} · quotes {ver.get('quotes_verified')}/{ver.get('quotes_total')} verified")
    out = {"computed": computed}
    if payload.get("mode", "analyze") != "full":
        return out

    # ---- citation_finder, step by step, per traceable revised value
    targets = computed["targets"]
    max_targets = int(payload.get("max_targets") or 3)
    n_t = min(len(targets), max_targets)
    frame("node.started", node="citation_finder", lane=0,
          action=f"{len(targets)} revised value(s) to trace" if targets else "no traceable revised value")
    affected: list[tuple[dict, dict]] = []
    stats = {"candidates": 0, "judged": 0}

    def on_progress(ev: dict) -> None:
        line = describe_event(ev)
        if line:
            frame("node.output", node="citation_finder", lane=0, summary=line)

    for i, t in enumerate(targets[:max_targets]):
        try:
            frame("node.active", node="citation_finder", lane=0, action=f"value {i + 1}/{n_t}: pre-screening citing papers in Europe PMC")
            state = agents.call("citation_finder", {"op": "start", "target": t}, on_progress)["state"]
            while state["phase"] != "done":
                frame("node.active", node="citation_finder", lane=0, action=f"value {i + 1}/{n_t}: {progress_line(state)}")
                state = agents.call("citation_finder", {"op": "step", "state": state}, on_progress)["state"]
            res = state["result"]
        except Exception as e:  # noqa: BLE001 -- one value failing does not stop the others
            log.exception("citation_finder failed for %s", t.get("target_id"))
            frame("node.output", node="citation_finder", lane=0, summary=f"value {i + 1}: failed ({type(e).__name__}: {e})"[:300])
            continue
        stats["candidates"] += res.get("n_candidates") or 0
        stats["judged"] += res.get("n_judged") or 0
        mine = [(t, w) for w in res.get("citing_works") or [] if w.get("cites") in nt.NOTIFY_CLASSES]
        affected += mine
        frame("node.output", node="citation_finder", lane=0,
              summary=f"value {i + 1}: {res.get('n_candidates')} candidate(s), {len(mine)} relying on the old value")
    skipped = max(0, len(targets) - max_targets)
    frame("node.done", node="citation_finder", lane=0, summary=
          f"{stats['candidates']} candidate citing paper(s), {stats['judged']} judged, {len(affected)} rely on a revised value"
          + (f" ({skipped} further value(s) not traced in this run)" if skipped else ""))

    # ---- notifier xN (drafts only; the caller sends)
    max_alerts = int(payload.get("max_alerts") or 5)
    acs = [store.affected_citation_doc(event["event_id"], t, "playground", w, "not_required", [])[1] for t, w in affected]
    if len(acs) > max_alerts:
        frame("node.output", node="notifier", lane=0, summary=f"{len(acs)} citing papers to alert; drafting the first {max_alerts}")
        acs = acs[:max_alerts]
    for lane, ac in enumerate(acs):
        frame("node.started", node="notifier", lane=lane,
              action=f"drafting alert (flash) for {(ac.get('citing_paper_title') or ac.get('work_id') or '')[:60]}")

    def draft(ac: dict) -> dict:
        try:
            return agents.call("notifier", {"ac": ac, "event": event})
        except Exception:  # noqa: BLE001 -- a failed draft falls back to the deterministic template
            log.exception("notifier failed for %s", ac.get("affected_citation_id"))
            subject, body = nt.template_draft(ac, event)
            return {"subject": subject, "body": body}

    with cf.ThreadPoolExecutor(max(1, len(acs))) as ex:
        drafted = list(ex.map(draft, acs))
    drafts = [{"lane": lane, "subject": d["subject"], "body": d["body"], "work_id": ac.get("work_id")}
              for lane, (ac, d) in enumerate(zip(acs, drafted))]
    for d in drafts:
        frame("node.active", node="notifier", lane=d["lane"], action=f"drafted: {d['subject'][:80]}")
    out["citation"] = {"n_targets": len(targets), "traced": n_t, "skipped": skipped, **stats, "n_affected": len(affected)}
    out["drafts"] = drafts
    return out


HANDLERS: dict[str, Callable[[dict, Emit], dict]] = {
    "claim_extractor": claim_extractor, "drift_analyzer": drift_analyzer, "citation_finder": citation_finder,
    "notifier": notifier, "supervisor": supervisor,
}
AGENT_NAMES = ("claim_extractor", "drift_analyzer", "citation_finder", "notifier", "supervisor")
