"""ClaimDrift 5-Agent Orchestration Playground backend.

One SSE endpoint that runs the REAL pipeline live on one preprint -> published pair and lights up the agents node by
node:

  GET /api/playground/orchestrate?email=<address>  (text/event-stream)

  claim_extractor x2 (flash, preprint v1 and published version in parallel)
    -> drift_analyzer (pro, one call over the two claim lists; abstract-level severity and quote verification alongside)
    -> citation_finder (citation orchestra: Europe PMC pre-screen, orchestrator + workers, verification; step by step)
    -> notifier xN (one alert per citing paper that relies on a revised value, mailed to the address the user typed)

The run is the supervisor agent in mode "full" (claimdrift.agent_handlers.supervisor): on Vertex AI Agent Engine when
CLAIMDRIFT_ENGINES is set (deployed), in-process otherwise (local). The supervisor streams the node frames below and
returns the alert drafts; sending stays here on Cloud Run (agents only compute, 部署方案 A7). Nothing is written to
Elasticsearch, so there is nothing to tear down. Mail goes to the address entered on the page (user decision
2026-10-02) -- unlike the production notifier, which only ever mails the test inbox -- capped at PLAYGROUND_MAX_EMAILS
per run, one run at a time.

Frames: run.started, pipeline.warming, pipeline.ready, node.started / node.active / node.output / node.done / node.error,
drift.minted, email.sending / email.sent / email.failed, run.error, run.complete.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncIterator

log = logging.getLogger("playground.orchestration")

# Backer et al.: mean incubation period 5.8 days (v1, 34 cases) -> 6.4 days (published, 88 cases); the citation gold has
# 17 citing papers that still rely on 5.8 days. Case-bank pair (full texts in the case bank), so no download is needed.
# (The earlier minocycline/NfL case turned out to be abstract-only condensing: the numbers are still in the published body.)
CASE = {
    "paper_id": "incubation_travellers_eurosurv",
    "preprint_doi": "10.1101/2020.01.27.20018986",
    "published_doi": "10.2807/1560-7917.ES.2020.25.5.2000062",
    "title": "The incubation period of 2019-nCoV infections among travellers from Wuhan, China",
}
PIPELINE = [
    {"id": "claim_extractor", "label": "Claim Extractor", "fanout": True},
    {"id": "drift_analyzer", "label": "Drift Analyzer", "fanout": False},
    {"id": "citation_finder", "label": "Citation Finder", "fanout": False},
    {"id": "notifier", "label": "Notifier", "fanout": True},
]
MAX_EMAILS = int(os.environ.get("PLAYGROUND_MAX_EMAILS", "5"))
MAX_TARGETS = int(os.environ.get("PLAYGROUND_MAX_TARGETS", "3"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_run_lock = asyncio.Lock()


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _fallback_alert(event: dict) -> tuple[str, str]:
    """One summary mail when no citing paper relies on a revised value (so the user still receives the drift report)."""
    lines = [f"Preprint {event.get('preprint_doi')} (v1) vs published version {event.get('published_doi')}", "",
             event.get("drift_summary") or "", "", "Changes found:"]
    for d in (event.get("claim_diffs") or [])[:8]:
        lines += [f"- [{d.get('severity_tier')}] {d.get('change_description') or ''}",
                  f"    v1:        {(d.get('preprint_text') or '')[:300]}",
                  f"    published: {(d.get('published_text') or '(no counterpart)')[:300]}"]
    lines += ["", "No citing paper with open full text was found that relies on a revised value, so no per-citation "
              "alert was generated.", "", "This message was generated automatically by the ClaimDrift Playground."]
    return f"[ClaimDrift] Drift report: {CASE['title'][:90]}", "\n".join(lines)


async def orchestrate(email: str) -> AsyncIterator[str]:
    yield _sse("run.started", {"case": {k: CASE[k] for k in ("preprint_doi", "published_doi", "title")},
                               "pipeline": PIPELINE, "judge_email": email})
    if not EMAIL_RE.match(email or ""):
        yield _sse("run.error", {"detail": "Please enter a valid e-mail address."})
        yield _sse("run.complete", {"event_id": None, "emails_sent": 0, "summary": None})
        return
    if _run_lock.locked():
        yield _sse("run.error", {"detail": "Another Playground run is in progress. Please try again in a few minutes."})
        yield _sse("run.complete", {"event_id": None, "emails_sent": 0, "summary": None})
        return
    async with _run_lock:
        async for frame in _run(email):
            yield frame


async def _run(email: str) -> AsyncIterator[str]:
    from claimdrift import engines, notifier, serial
    from claimdrift.documents import fetch_pair, load_pair

    t0 = time.time()
    event: dict | None = None
    emails_sent = 0
    try:
        # ---- full texts (case bank / docstore; the supervisor receives them with the request)
        yield _sse("pipeline.warming", {"detail": "loading preprint v1 and published full texts…"})
        pair = await asyncio.to_thread(lambda: load_pair(CASE["paper_id"]) if CASE.get("paper_id")
                                       else fetch_pair(CASE["preprint_doi"], CASE["published_doi"]))
        where = "Vertex AI Agent Engine" if engines.configured() else "in-process"
        yield _sse("pipeline.ready", {"detail": f"full texts loaded (~{pair.est_tokens():,} tokens); supervisor on {where}"})

        # ---- supervisor (mode full): relay its node frames while it runs
        loop = asyncio.get_running_loop()
        frames: asyncio.Queue = asyncio.Queue()

        def on_progress(msg: dict) -> None:
            if isinstance(msg, dict) and msg.get("frame"):
                loop.call_soon_threadsafe(frames.put_nowait, (msg["frame"], msg.get("data") or {}))

        payload = {"mode": "full", "pair": serial.pair_to_dict(pair), "max_targets": MAX_TARGETS, "max_alerts": MAX_EMAILS}
        task = asyncio.ensure_future(asyncio.to_thread(engines.agents().call, "supervisor", payload, on_progress))
        while True:
            getter = asyncio.ensure_future(frames.get())
            done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                name, data = getter.result()
                if name == "drift.minted":
                    event = {"event_id": data.get("event_id"), "drift_summary": data.get("drift_summary")}
                yield _sse(name, data)
                continue
            getter.cancel()
            while not frames.empty():
                name, data = frames.get_nowait()
                yield _sse(name, data)
            break
        out = task.result()  # raises if the supervisor failed (its node.error frame was already relayed)
        event = out["computed"]["event"]

        # ---- notifier xN -> the address typed on the page (drafted by the notifier agent, sent here)
        drafts = out.get("drafts") or []
        if not drafts:
            subject, body = _fallback_alert(event)
            yield _sse("node.started", {"node": "notifier", "lane": 0, "action": "drift report"})
            drafts = [{"lane": 0, "subject": subject, "body": body}]
        for d in drafts:
            yield _sse("email.sending", {"to": email, "subject": d["subject"]})
            try:
                msg_id = await asyncio.to_thread(notifier.send_mail, email, d["subject"], d["body"])
                emails_sent += 1
                yield _sse("email.sent", {"to": email, "subject": d["subject"], "message_id": msg_id})
                yield _sse("node.done", {"node": "notifier", "lane": d["lane"], "summary": f"sent: {d['subject'][:80]}"})
            except Exception as e:  # noqa: BLE001
                log.exception("playground mail failed")
                yield _sse("email.failed", {"to": email, "subject": d["subject"], "error": f"{e}"[:300]})
                yield _sse("node.error", {"node": "notifier", "lane": d["lane"], "message": f"{e}"[:200]})
    except Exception as e:  # noqa: BLE001
        log.exception("playground run failed")
        yield _sse("run.error", {"detail": f"The pipeline stopped: {type(e).__name__}: {e}"[:500]})
    yield _sse("run.complete", {"event_id": event and event.get("event_id"), "emails_sent": emails_sent,
                                "summary": event and event.get("drift_summary"), "secs": round(time.time() - t0)})
