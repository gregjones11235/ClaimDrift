"""Main pipeline (layer ①): a hard-coded DAG, not an agent.

  load v1 + published JATS  ->  drift_analyzer (stuffed | claims route)  ->  abstract-level severity  ->
  事后校验 via MCP verify_quote  ->  review triggers  ->  drift_events (+ claims for the long branch)  ->
  enqueue citation runs (layer ②, asynchronous: one target takes ~7 min, beyond the 600 s main-pipeline budget)

Split for the deployment on Agent Engine (部署方案 A2/A7): compute() has no side effects and is what the supervisor
agent runs; persist() writes ES and queues the citation runs and always runs on the caller's side (Cloud Run / local).
With CLAIMDRIFT_ENGINES set, analyze_pair() sends the pair to the supervisor engine instead of computing in-process.
"""
from __future__ import annotations

import concurrent.futures as cf
import logging

from . import abstract_severity, config, drift_analyzer, es, llm, provenance, review, selfcheck, store
from .citations import jobs as citation_jobs
from .citations.targets import build_targets
from .documents import Pair, fetch_pair, load_pair

log = logging.getLogger("claimdrift.pipeline")


def compute(pair: Pair, mcp_client=None, force_route: str | None = None, analysis: dict | None = None,
            claims: dict | None = None, extract_usage: dict | None = None, abs_sev: dict | None = None) -> dict:
    """Layer ① without side effects -> {"event", "targets", "analysis"}.
    `analysis`: a drift_analyzer result produced earlier with the same prompt and model (e.g. an experiment run, or the
    drift_analyzer engine); when given, the drift_analyzer call is skipped. `claims`: the two claim lists, already
    extracted (the Playground / supervisor run the two extractors as separate nodes). `abs_sev`: abstract-level
    severity computed alongside by the caller."""
    with cf.ThreadPoolExecutor(2) as ex:  # the two pro calls are independent
        f_drift = ex.submit(drift_analyzer.analyze, pair, llm.pro(), force_route, claims=claims,
                            extract_usage=extract_usage) if analysis is None else None
        f_abs = ex.submit(abstract_severity.classify, llm.pro(), pair.preprint.abstract, pair.published.abstract) if abs_sev is None else None
        analysis = f_drift.result() if f_drift else analysis
        abs_sev = f_abs.result() if f_abs else abs_sev
    if analysis["output"] is None:
        raise RuntimeError(f"{pair.paper_id}: drift_analyzer returned no parseable JSON; raw tail: {analysis['raw_tail'][-300:]}")
    own_client = mcp_client is None
    if own_client:
        from .mcp_client import McpClient
        mcp_client = McpClient()
    try:
        prov, ver = provenance.check(analysis["output"], pair, mcp_client=mcp_client)
    finally:
        if own_client:
            mcp_client.close()
    event = store.drift_event_doc(pair, analysis, abs_sev, prov, ver)
    review.apply_event(event)
    targets = build_targets(event, pair.paper_id, pair.title, pair.first_author)
    return {"event": event, "targets": targets, "analysis": analysis}


def persist(pair: Pair, computed: dict, write: bool = True, enqueue_citations: bool = True) -> dict:
    """Side effects of layer ①: review carry-over, drift_events, the self-check index, claims, citation runs."""
    event, targets, analysis = computed["event"], computed["targets"], computed["analysis"]
    existing = store.get_event(event["event_id"]) if write else None
    if existing and existing.get("review_status") in ("approved", "rejected"):
        # a re-analysis replaces machine output; a previous human decision no longer applies to it, but is kept visible
        event["review_reasons"] = ["reanalyzed_after_review"]
        event["review_note"] = f"previous decision {existing['review_status']} by {existing.get('reviewer')} at {existing.get('reviewed_at')}"
        review.apply_event(event)
    event["citation_analysis"] = {"status": "queued" if (targets and enqueue_citations) else ("no_target" if not targets else "not_queued"),
                                  "n_targets": len(targets), "run_ids": [], "checked_until": None, "n_superseded": None,
                                  "coverage_complete": None, "updated_at": store.now()}
    if write:
        store.write_event(event)
        try:
            selfcheck.index_event(event)  # P1.10 search index: one doc per change, old text first
        except es.ESError:
            log.warning("selfcheck index %s unavailable; run `python -m claimdrift setup-es`", selfcheck.INDEX)
        if analysis["claims"]:
            es.bulk_index(config.INDICES["claims"], store.claims_docs(event["event_id"], pair, analysis["claims"]))
        if targets and enqueue_citations:
            run_ids = [citation_jobs.enqueue(t, kind="initial") for t in targets]
            es.update(config.INDICES["drift_events"], event["event_id"], {"citation_analysis": event["citation_analysis"] | {"run_ids": run_ids}})
            event["citation_analysis"]["run_ids"] = run_ids
    ver = event["verification"]
    log.info("%s: route=%s diffs=%d fulltext=%s abstract=%s quotes=%d/%d review=%s targets=%d", pair.paper_id,
             analysis["route"]["route"], len(event["claim_diffs"]), event["fulltext_severity"]["tier"],
             event["abstract_severity"].get("class"), ver["quotes_verified"], ver["quotes_total"], event["review_status"], len(targets))
    event["_analysis"] = {"secs": analysis["secs"], "raw_tail": analysis["raw_tail"], "targets": targets}
    return event


def analyze_pair(pair: Pair, mcp_client=None, force_route: str | None = None, write: bool = True,
                 enqueue_citations: bool = True, analysis: dict | None = None, on_progress=None) -> dict:
    """Run layer ① for one pair and return the drift_event document (written to ES unless write=False).
    on_progress(dict): the same node frames the Playground shows ({"frame": ..., "data": {...}}), so a caller that
    keeps a user waiting (the author self-check's on-demand analysis) can show where the run is."""
    from . import agent_handlers, engines, serial
    report = on_progress or (lambda _m: None)
    if analysis is None:
        # the supervisor (on Agent Engine when configured, else in-process) runs extractor x2 -> analyzer -> verification
        payload = {"mode": "analyze", "pair": serial.pair_to_dict(pair), "force_route": force_route}
        if engines.configured():
            computed = engines.call_remote("supervisor", payload, report)["computed"]
        else:
            computed = agent_handlers.supervisor(payload, report, agents=engines.LocalAgents())["computed"]
    else:
        computed = compute(pair, mcp_client=mcp_client, force_route=force_route, analysis=analysis)
    report({"frame": "pipeline.saving", "data": {"summary": "writing the drift event and queueing the citation analysis"}})
    return persist(pair, computed, write=write, enqueue_citations=enqueue_citations)


def analyze_paper(paper_id: str, **kw) -> dict:
    return analyze_pair(load_pair(paper_id), **kw)


def analyze_dois(preprint_doi: str, published_doi: str | None = None, server_hint: str | None = None, **kw) -> dict:
    """On-demand path (layer ③ and the dispatcher): fetch v1 + published JATS into the docstore, then analyse."""
    report = kw.get("on_progress") or (lambda _m: None)
    report({"frame": "pipeline.fetching", "data": {"summary": "fetching the v1 JATS (bioRxiv/medRxiv) and the published full text (Europe PMC)"}})
    pair = fetch_pair(preprint_doi, published_doi, server_hint)
    report({"frame": "pipeline.ready", "data": {"summary": f"full texts loaded (~{pair.est_tokens():,} tokens)"}})
    return analyze_pair(pair, **kw)
