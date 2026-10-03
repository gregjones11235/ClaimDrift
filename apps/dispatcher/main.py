"""ClaimDrift dispatcher service -- the automatic data pipeline's entry into layer ① (新系统云端部署.md §4.6).

  pullers (Cloud Run Jobs, Cloud Scheduler) -> ES `preprints` (pairs with a published_doi)
    -> Elastic Workflow `dispatch_new_pairs` (every 5 min, watermark in dispatch_state)
    -> POST /dispatch (bearer token)  -> Pub/Sub topic `claimdrift-dispatch`
    -> push subscription -> POST /run (Google OIDC) -> claimdrift.pipeline.analyze_dois
         v1 JATS + published JATS -> supervisor on Vertex AI Agent Engine (claim_extractor x2 -> drift_analyzer ->
         quote verification via MCP) -> drift_events + review triggers -> citation runs queued (the
         claimdrift-citation-dispatch job analyses citing papers and notifies the test inbox)

/dispatch returns in ~100 ms (Elastic Workflows' http step has a fixed 60 s limit on Serverless); /run holds the
request for the whole analysis (Pub/Sub ack deadline 600 s); up to 4 requests per instance, since /run mostly waits on Agent Engine.

Budget (cost control): `dispatch_state/_doc/dispatch_budget` holds budget_max / budget_used. /dispatch first pre-checks
the pair (v1 JATS available, published full text open in Europe PMC; network only, no model) -- pairs that cannot be
analysed are answered 200 and cost nothing. Each analysable pair published to Pub/Sub uses one unit. Once used >= max,
the dispatcher pauses the whole pipeline by itself: the puller schedules (PAUSE_SCHEDULER_JOBS) and the Elastic Workflow
(disabled, so a paused pipeline produces no runs and no failure alerts), and answers the pairs still in flight with 200
"paused". Those pairs are not lost: `bash deploy/pipeline.sh batch N` rewinds the workflow's watermark to just before the
oldest pair without a drift event before it re-enables everything. The workflow calls /dispatch one pair at a time
(serial foreach), so the count is exact.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from google.auth.transport import requests as google_auth_requests
from google.cloud import pubsub_v1
from google.oauth2 import id_token
from pydantic import BaseModel

# Repo layout in the image and locally: /app (or the repo root) holds claimdrift/ and apps/dispatcher/.
for _p in Path(__file__).resolve().parents:
    if (_p / "claimdrift").is_dir():
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
        break

from claimdrift import es as cd_es  # noqa: E402
from claimdrift import store  # noqa: E402
from apps.dispatcher.versions import fetch_first_version  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dispatcher")

# --- Config -----------------------------------------------------------------
GCP_PROJECT = os.environ["GCP_PROJECT"]
WF_BEARER_TOKEN = os.environ["WF_BEARER_TOKEN"]
PUBSUB_TOPIC = os.environ.get("PUBSUB_TOPIC", "claimdrift-dispatch")
# Service account Pub/Sub signs the push OIDC token with (--push-auth-service-account on the subscription)
PUBSUB_PUSH_SA_EMAIL = os.environ["PUBSUB_PUSH_SA_EMAIL"]
# Expected `aud` of that token; Pub/Sub defaults it to the push endpoint URL
PUBSUB_PUSH_AUDIENCE = os.environ.get("PUBSUB_PUSH_AUDIENCE") or None
STATE_INDEX, BUDGET_ID = "dispatch_state", "dispatch_budget"
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")
# Cloud Scheduler jobs of the pullers, paused automatically once the dispatch budget is used up
PAUSE_SCHEDULER_JOBS = [j for j in os.environ.get("PAUSE_SCHEDULER_JOBS", "").split(",") if j]
# Kibana of the Serverless project + the workflow to disable at the same moment (API key = the ES key)
KIBANA_URL = (os.environ.get("KIBANA_URL") or "").rstrip("/")
WORKFLOW_ID = os.environ.get("WORKFLOW_ID", "dispatch-new-pairs")
_paused_once = False

app = FastAPI(title="claimdrift-dispatcher")
_publisher: pubsub_v1.PublisherClient | None = None


class DispatchRequest(BaseModel):
    published_doi: str
    preprint_doi: str


class _AsyncES:
    """The two calls versions.fetch_first_version makes, on claimdrift's stdlib client (works on Serverless)."""

    async def search(self, index: str, size: int = 10, query: dict | None = None, **_):
        return await asyncio.to_thread(cd_es.request, "POST", f"{index}/_search", {"size": size, "query": query or {"match_all": {}}})

    async def index(self, index: str, id: str, document: dict, refresh: str | None = None):  # noqa: A002
        return await asyncio.to_thread(cd_es.put, index, id, document, refresh or "false")


def get_publisher() -> pubsub_v1.PublisherClient:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


# --- Budget -------------------------------------------------------------------
def budget() -> dict:
    """{"budget_max", "budget_used", ...}; no doc = no budget configured = unlimited."""
    try:
        return cd_es.source(STATE_INDEX, BUDGET_ID) or {}
    except cd_es.ESError:
        log.exception("budget read failed; treating as exhausted (fail closed)")
        return {"budget_max": 0, "budget_used": 0}


def budget_left(b: dict) -> int | None:
    if "budget_max" not in b:
        return None
    return max(0, int(b.get("budget_max") or 0) - int(b.get("budget_used") or 0))


def use_budget(pair_key: str) -> None:
    """Count one dispatched pair (idempotent per pair)."""
    b = budget()
    if "budget_max" not in b:
        return
    pairs = list(b.get("analyzed_pairs") or [])
    if pair_key in pairs:
        return
    cd_es.put(STATE_INDEX, BUDGET_ID, b | {"budget_used": int(b.get("budget_used") or 0) + 1, "analyzed_pairs": pairs + [pair_key],
                                           "last_updated_at": store.now()})


def set_workflow_enabled(enabled: bool) -> None:
    """Kibana has no enable/disable endpoint for workflows: fetch the stored YAML, flip its top-level `enabled:` line,
    upload it again with overwrite=true (the call deploy/pipeline.sh uses)."""
    import re
    import urllib.request
    from claimdrift import config as cd_config
    if not KIBANA_URL:
        return
    headers = {"Authorization": f"ApiKey {cd_config.ES_API_KEY}", "kbn-xsrf": "true", "x-elastic-internal-origin": "Kibana",
               "Content-Type": "application/json"}

    def call(method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(KIBANA_URL + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")

    wf = next((w for w in call("GET", "/api/workflows?size=100").get("results", []) if w.get("id") == WORKFLOW_ID), None)
    if wf is None:
        log.warning("workflow %s not found in Kibana", WORKFLOW_ID)
        return
    yaml = re.sub(r"(?m)^enabled: (true|false)$", f"enabled: {'true' if enabled else 'false'}", wf["yaml"])
    call("POST", "/api/workflows?overwrite=true", {"workflows": [{"id": WORKFLOW_ID, "yaml": yaml}]})
    log.info("workflow %s %s", WORKFLOW_ID, "enabled" if enabled else "disabled")


def pause_pullers() -> None:
    """Pause the pipeline (once per process): puller schedules paused, workflow disabled. The batch is complete; nothing
    more is pulled or dispatched until the next `deploy/pipeline.sh batch`."""
    global _paused_once
    if _paused_once:
        return
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(creds)
    for job in PAUSE_SCHEDULER_JOBS if PAUSE_SCHEDULER_JOBS else []:
        url = f"https://cloudscheduler.googleapis.com/v1/projects/{GCP_PROJECT}/locations/{GCP_REGION}/jobs/{job}:pause"
        r = session.post(url, timeout=30)
        log.info("budget exhausted: pause scheduler %s -> HTTP %s", job, r.status_code)
    try:
        set_workflow_enabled(False)
    except Exception:  # noqa: BLE001
        log.exception("could not disable workflow %s", WORKFLOW_ID)
    _paused_once = True


# --- Endpoints ------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    # NOT /healthz or /_ah/*: reserved paths intercepted by Cloud Run's frontend
    b = budget()
    return {"status": "ok", "budget_left": budget_left(b), "budget": {k: b.get(k) for k in ("budget_max", "budget_used")}}


@app.post("/dispatch")
async def dispatch(req: DispatchRequest, authorization: str = Header(...), force: bool = False) -> dict:
    """Bearer auth -> budget -> idempotency (a drift_event for this pair already exists) -> publish to Pub/Sub."""
    if authorization != f"Bearer {WF_BEARER_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid bearer token")
    b = await asyncio.to_thread(budget)
    if budget_left(b) == 0:
        # 200: no failed workflow run / alert. The pair is not lost: the next batch rewinds the watermark before it.
        log.info("dispatch deferred (budget %s/%s used): preprint=%s", b.get("budget_used"), b.get("budget_max"), req.preprint_doi)
        try:
            await asyncio.to_thread(pause_pullers)
        except Exception:  # noqa: BLE001 -- pausing is best effort; the budget already stops all spending
            log.exception("could not pause the pipeline")
        return {"status": "paused_budget_exhausted"}
    if not force:
        existing = await asyncio.to_thread(_find_existing_drift_event, req.preprint_doi, req.published_doi)
        if existing:
            return {"status": "already_processed", "drift_event_id": existing}
    from claimdrift.selfcheck import precheck_doi  # bioRxiv API + Europe PMC search, a few seconds
    pre = await asyncio.to_thread(precheck_doi, req.preprint_doi)
    if pre["status"] not in ("ready", "check_failed"):
        log.info("dispatch skipped (%s): preprint=%s", pre["status"], req.preprint_doi)
        return {"status": "not_analysable", "reason": pre["status"]}
    payload = req.model_dump() | ({"force": True} if force else {})
    publisher = get_publisher()
    # resolve the future: a failed publish must surface as non-2xx, or the workflow's watermark skips the pair
    message_id = await asyncio.to_thread(publisher.publish(publisher.topic_path(GCP_PROJECT, PUBSUB_TOPIC),
                                                           json.dumps(payload).encode()).result, timeout=15)
    await asyncio.to_thread(use_budget, f"{req.preprint_doi.lower()}|{req.published_doi.lower()}")
    log.info("dispatch enqueued: preprint=%s published=%s message_id=%s", req.preprint_doi, req.published_doi, message_id)
    return {"status": "enqueued", "message_id": message_id}


@app.post("/run")
async def run(request: Request, authorization: str = Header(...)) -> dict:
    """Pub/Sub push target. 2xx on success and on dropped-by-design messages (Pub/Sub acks, no retry)."""
    _verify_pubsub_oidc(authorization, request)
    msg = ((await request.json()) or {}).get("message") or {}
    try:
        payload = json.loads(base64.b64decode(msg.get("data") or "").decode("utf-8"))
        req = DispatchRequest(**{k: payload[k] for k in ("preprint_doi", "published_doi")})
    except Exception:  # noqa: BLE001
        log.exception("pubsub payload unusable; acking and dropping")
        return {"status": "dropped_invalid"}
    if not payload.get("force") and await asyncio.to_thread(_find_existing_drift_event, req.preprint_doi, req.published_doi):
        return {"status": "already_processed"}
    log.info("run accepted: preprint=%s published=%s message_id=%s", req.preprint_doi, req.published_doi, msg.get("messageId"))
    status = await run_pipeline(req)
    return {"status": status}


def _verify_pubsub_oidc(authorization: str, request: Request) -> None:
    """Only our own subscription may trigger /run: Google-signed token, aud = push URL, email = push service account.
    (The service allows unauthenticated invocations because /dispatch uses the workflow's bearer token.)"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing oidc bearer")
    audience = PUBSUB_PUSH_AUDIENCE or str(request.url).split("?", 1)[0]
    try:
        claims = id_token.verify_oauth2_token(authorization[7:], google_auth_requests.Request(), audience=audience)
    except ValueError:
        log.exception("pubsub oidc token failed verification (audience=%s)", audience)
        raise HTTPException(status_code=401, detail="invalid oidc token")
    if claims.get("email") != PUBSUB_PUSH_SA_EMAIL:
        log.warning("pubsub oidc email mismatch: got %s, expected %s", claims.get("email"), PUBSUB_PUSH_SA_EMAIL)
        raise HTTPException(status_code=403, detail="unauthorized pubsub signer")


def _find_existing_drift_event(preprint_doi: str, published_doi: str) -> str | None:
    """Idempotency gate: the (preprint_doi, published_doi) pair already has a drift_event."""
    try:
        r = cd_es.request("POST", "drift_events/_search", {"size": 1, "_source": ["event_id"], "query": {"bool": {"filter": [
            {"term": {"preprint_doi": preprint_doi.lower()}}, {"term": {"published_doi": published_doi.lower()}}]}}})
    except cd_es.ESError:
        log.exception("idempotency check failed; proceeding as if no prior event")
        return None
    hits = r["hits"]["hits"]
    return hits[0]["_source"].get("event_id") if hits else None


async def run_pipeline(req: DispatchRequest) -> str:
    """Layer ① for one pair (claimdrift.pipeline.analyze_dois): v1 from its OWN JATS (never the API abstract, which is
    the latest version's), published full text from Europe PMC, analysis on the supervisor engine, event written, citation
    runs queued. Notifications are sent later by the citation job (test inbox only), never from here."""
    try:
        preprint, info = await fetch_first_version(_AsyncES(), req.preprint_doi.lower())
        if info.get("withdrawn"):
            log.warning("preprint=%s has withdrawn version(s) %s", req.preprint_doi, info["withdrawn"])
        if preprint is None:
            log.warning("pipeline aborted: v1 text unavailable for preprint=%s (versions %s)", req.preprint_doi, info.get("versions"))
            return "aborted_no_v1"
        from claimdrift import pipeline  # lazy: heavy imports
        event = await asyncio.to_thread(pipeline.analyze_dois, req.preprint_doi, req.published_doi, preprint.get("source"))
        log.info("drift_event %s written: fulltext=%s abstract=%s quotes=%s/%s review=%s citation=%s runs=%s",
                 event["event_id"], event["fulltext_severity"]["tier"], (event.get("abstract_severity") or {}).get("class"),
                 event["verification"]["quotes_verified"], event["verification"]["quotes_total"], event["review_status"],
                 event["citation_analysis"].get("status"), event["citation_analysis"].get("run_ids"))
        return "completed"
    except LookupError as e:  # no v1 / no open published full text: expected for many pairs
        log.warning("pipeline skipped for preprint=%s: %s", req.preprint_doi, e)
        return "skipped_not_analysable"
    except Exception:  # noqa: BLE001 -- logged; Pub/Sub must not redeliver a pair that failed deterministically
        log.exception("pipeline failed for preprint=%s", req.preprint_doi)
        return "failed"
