"""ClaimDrift dispatcher service.

Two endpoints, both POST:

  /dispatch  — invoked by the scheduled Elastic Workflow with a pair of DOIs.
               Authenticates via bearer token, idempotency-checks against
               drift_events, then publishes a message to the Pub/Sub topic
               `claimdrift-dispatch` and returns 202 in ~100ms. This stays
               well within Elastic Workflows' 60s connector hard-limit (which
               is platform-fixed on Serverless — see contracts §9.6.1).

  /run       — Pub/Sub push subscription target. Verifies the Google-signed
               OIDC token in the Authorization header, then awaits the full
               ~200s pipeline (ES reverse-lookup -> supervisor stream -> ES
               bulk-write -> Gmail send). Returning 200 only after the
               pipeline finishes means each Cloud Run instance is visibly
               busy to the autoscaler (it counts in-flight HTTP requests),
               so concurrent pushes from Pub/Sub scale the service out to
               --max-instances. Pub/Sub's ack deadline (600s, set in the
               subscription) accommodates the pipeline runtime.

The two-endpoint split is what lets Elastic Workflows fire a backlog of
pairs in <1s each (workflow constraint) while still giving each pipeline
its full runtime budget (200s LLM streaming + ES writes + Gmail sends).

See apps/dispatcher/README.md for endpoints/config/deploy and
docs/contracts.md §9.6.1 for the inverted-topology rationale.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, AsyncIterator

import vertexai
from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk
from fastapi import FastAPI, Header, HTTPException, Request, Response
from google.auth.transport import requests as google_auth_requests
from google.cloud import pubsub_v1, secretmanager
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pydantic import BaseModel
from vertexai import agent_engines

# Translator is shared with the BFF; living in apps/bff keeps the BFF the
# canonical owner of the §6.1 envelope shape (it's the frontend-facing service).
# The dispatcher just imports the pure function to write live envelopes to ES.
#
# Two layouts to support:
#   - Local dev: <repo>/apps/dispatcher/main.py — parents[2] is the repo root,
#     and adding it to sys.path lets `from apps.bff.sse_adapter import …` work.
#   - Cloud Run image: /app/main.py + /app/apps/bff/sse_adapter.py — cwd
#     (/app) is already on sys.path; nothing to add. parents[2] would raise
#     IndexError here because /app only has two parents, so we guard it.
import sys as _sys
_here = Path(__file__).resolve()
for candidate in _here.parents:
    if (candidate / "apps" / "bff" / "sse_adapter.py").exists():
        if str(candidate) not in _sys.path:
            _sys.path.insert(0, str(candidate))
        break
from apps.bff.sse_adapter import TranslatorState, translate_adk_event  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dispatcher")

# --- Config (read once at startup) -----------------------------------------

GCP_PROJECT = os.environ["GCP_PROJECT"]
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")
SUPERVISOR_REASONING_ENGINE_ID = os.environ["SUPERVISOR_REASONING_ENGINE_ID"]
ELASTIC_ENDPOINT = os.environ["ELASTIC_ENDPOINT"]
ELASTIC_API_KEY = os.environ["ELASTIC_API_KEY"]
WF_BEARER_TOKEN = os.environ["WF_BEARER_TOKEN"]
USE_STUB_STREAM = bool(os.environ.get("USE_STUB_STREAM"))
# Pub/Sub topic that /dispatch publishes to and the push subscription delivers to /run.
PUBSUB_TOPIC = os.environ.get("PUBSUB_TOPIC", "claimdrift-dispatch")
# Service-account email that Pub/Sub uses to sign OIDC tokens for push delivery
# (configured on the subscription via --push-auth-service-account). /run checks
# the verified token's `email` claim equals this value before processing the
# pipeline. Same identity as the dispatcher's own SA in our deployment.
PUBSUB_PUSH_SA_EMAIL = os.environ.get(
    "PUBSUB_PUSH_SA_EMAIL",
    "751133713115-compute@developer.gserviceaccount.com",
)
# Expected `aud` claim in the OIDC token. Google's Pub/Sub push generator
# defaults audience = the push endpoint URL; we match against that.
PUBSUB_PUSH_AUDIENCE = os.environ.get("PUBSUB_PUSH_AUDIENCE") or None
# Demo helper: when set, any notification whose recipient_email resolves to None
# (the §3.3 v0 limitation — citing_paper_authors[].email is always null at the
# citation_finder boundary) gets routed to this fallback inbox so the demo
# pipeline visibly delivers mail. Leave unset in production.
DEMO_FALLBACK_EMAIL = os.environ.get("DEMO_FALLBACK_EMAIL") or None

STUB_STREAM_PATH = Path(__file__).parent / "_stub_stream.json"

GMAIL_SECRETS = {
    "refresh_token": "gmail-refresh-token",
    "client_id": "gmail-oauth-client-id",
    "client_secret": "gmail-oauth-client-secret",
}

# --- App --------------------------------------------------------------------

app = FastAPI(title="claimdrift-dispatcher")


class DispatchRequest(BaseModel):
    published_doi: str
    preprint_doi: str


@app.get("/health")
def health() -> dict:
    # NOTE: do NOT use /healthz or /_ah/* — these are GFE-reserved paths
    # intercepted by Cloud Run's frontend before reaching the container.
    return {"status": "ok"}


@app.post("/dispatch")
async def dispatch(
    req: DispatchRequest,
    authorization: str = Header(...),
    force: bool = False,
) -> dict:
    """Thin enqueue endpoint. Auth + idempotency check + publish to Pub/Sub.

    Returns 202 in ~100ms. The actual pipeline runs in /run, triggered by the
    Pub/Sub push subscription. This split exists because Elastic Workflows'
    http step has a hardcoded 60s connector timeout on Serverless (not user-
    configurable), but our pipeline runs ~200s. See module docstring.
    """
    # (a) Bearer auth — Elastic Workflows has no central secret store on 9.3,
    # so this token is inlined into the workflow YAML (gitignored .yaml,
    # template-only in git). Validation here.
    if authorization != f"Bearer {WF_BEARER_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid bearer token")

    # (b) Idempotency check — skip if this (preprint_doi, published_doi) pair
    # already produced a drift_event. Pub/Sub is at-least-once + the workflow
    # polls every 5 min, so duplicates are realistic. Check HERE (not in /run)
    # so we don't even enqueue a message we know will be dropped — saves one
    # Pub/Sub round-trip per duplicate. Override with ?force=true for explicit
    # re-runs.
    if not force:
        existing = await _find_existing_drift_event(req.preprint_doi, req.published_doi)
        if existing:
            log.info(
                "dispatch skipped (idempotent): pair already processed in drift_event=%s",
                existing,
            )
            return {"status": "already_processed", "drift_event_id": existing}

    # (c) Publish to Pub/Sub. The push subscription delivers to /run.
    payload = req.model_dump()
    if force:
        payload["force"] = True
    data = json.dumps(payload).encode("utf-8")
    publisher = get_publisher()
    topic_path = publisher.topic_path(GCP_PROJECT, PUBSUB_TOPIC)
    # publish() returns a concurrent.futures.Future; resolving it gives the
    # message_id and confirms Pub/Sub accepted the message. Don't fire and
    # forget — if the publish itself fails (e.g. IAM) the workflow needs to
    # know via a non-2xx response, otherwise the watermark advances over a
    # pair that was never processed.
    future = publisher.publish(topic_path, data)
    message_id = await asyncio.to_thread(future.result, timeout=15)

    log.info(
        "dispatch enqueued: preprint=%s published=%s force=%s message_id=%s",
        req.preprint_doi, req.published_doi, force, message_id,
    )
    return {"status": "enqueued", "message_id": message_id}


@app.post("/run")
async def run(request: Request, authorization: str = Header(...)) -> dict:
    """Pub/Sub push target — actually executes the pipeline.

    Verifies the Google-signed OIDC token (Pub/Sub signs one per push using
    the SA configured via --push-auth-service-account), then awaits the full
    pipeline. The Pub/Sub push subscription's ack-deadline=600 (set in
    `gcloud pubsub subscriptions create`) gives us the runtime budget;
    --concurrency=1 + --cpu-boost on the Cloud Run service means each
    instance handles one pipeline at a time and the autoscaler sees an
    in-flight request, so concurrent pushes scale out to --max-instances.

    Returns 2xx on success and on dropped-by-design failures (parse errors,
    upstream pipeline aborts); Pub/Sub will ack and not retry. Returns 5xx
    only for transient errors where retry is the right move (currently:
    nothing — we treat the pipeline's internal try/except as authoritative).
    """
    _verify_pubsub_oidc(authorization, request)

    envelope = await request.json()
    msg = (envelope or {}).get("message") or {}
    data_b64 = msg.get("data") or ""
    if not data_b64:
        log.warning("pubsub push had empty data; acking and dropping: %s", envelope)
        return {"status": "dropped_empty"}
    try:
        decoded = base64.b64decode(data_b64).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        log.exception("pubsub push payload not valid JSON; acking and dropping")
        return {"status": "dropped_unparseable"}

    try:
        req = DispatchRequest(**{k: v for k, v in payload.items() if k in {"preprint_doi", "published_doi"}})
    except Exception:
        log.exception("pubsub payload missing required fields; acking and dropping: %s", payload)
        return {"status": "dropped_invalid"}

    log.info(
        "run accepted: preprint=%s published=%s stub=%s message_id=%s",
        req.preprint_doi, req.published_doi, USE_STUB_STREAM,
        msg.get("messageId"),
    )

    # Sweep is fire-and-forget; cheap delete_by_query against agent_events
    # retention horizon. Each /run touches it once.
    asyncio.create_task(sweep_agent_events_retention())
    await run_pipeline(req)
    return {"status": "completed"}


def _verify_pubsub_oidc(authorization: str, request: Request) -> None:
    """Validate the Authorization header on a /run request.

    Pub/Sub push delivery uses Google-signed OIDC tokens. We verify the
    signature (via google.oauth2.id_token) and assert two claims:
      1. email == PUBSUB_PUSH_SA_EMAIL (the SA the subscription is configured
         to sign as). This prevents anything other than our own subscription
         from triggering /run, even though the Cloud Run service allows
         allUsers (kept for /dispatch's bearer-token model).
      2. aud == the push endpoint URL (or PUBSUB_PUSH_AUDIENCE override).
         id_token.verify_oauth2_token enforces this internally when we pass
         the expected audience.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing oidc bearer")
    token = authorization[len("Bearer "):]

    # Audience defaults to the push endpoint URL (Pub/Sub's default behavior).
    # Reconstruct it from the request unless PUBSUB_PUSH_AUDIENCE is set
    # explicitly (e.g. for testing with custom audience).
    audience = PUBSUB_PUSH_AUDIENCE or str(request.url).split("?", 1)[0]
    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_auth_requests.Request(),
            audience=audience,
        )
    except ValueError:
        log.exception("pubsub oidc token failed verification (audience=%s)", audience)
        raise HTTPException(status_code=401, detail="invalid oidc token")

    if claims.get("email") != PUBSUB_PUSH_SA_EMAIL:
        log.warning(
            "pubsub oidc email mismatch: got %s, expected %s",
            claims.get("email"), PUBSUB_PUSH_SA_EMAIL,
        )
        raise HTTPException(status_code=403, detail="unauthorized pubsub signer")


async def _find_existing_drift_event(preprint_doi: str, published_doi: str) -> str | None:
    """Return the event_id of any prior drift_event for this exact pair, or None.

    Used as the dispatcher's idempotency gate. Matches §2.2.3 schema: the pair
    `(preprint_doi, published_doi)` is the natural compound key for a drift
    event even though the document's `_id` is a UUID. We deliberately do NOT
    exclude `record_source=demo_seed` here — if the demo seed already wrote a
    drift_event for a pair, that's still "processed" by the system's lights.
    """
    try:
        resp = await get_es().search(
            index="drift_events",
            size=1,
            query={
                "bool": {
                    "filter": [
                        {"term": {"preprint_doi": preprint_doi}},
                        {"term": {"published_doi": published_doi}},
                    ]
                }
            },
            _source=["event_id"],
        )
    except Exception:
        log.exception("idempotency check failed; proceeding as if no prior event")
        return None
    hits = resp["hits"]["hits"]
    return hits[0]["_source"].get("event_id") if hits else None


async def run_pipeline(req: DispatchRequest) -> None:
    """End-to-end pipeline: ES read -> supervisor -> ES write -> Gmail send.

    Filled in across Step 2-6. Any exception here is caught + logged; it cannot
    propagate back to the HTTP caller (handler already returned 202).
    """
    try:
        # Step 2: ES reverse-lookup
        preprint = await fetch_preprint(req.preprint_doi)
        published = await fetch_preprint(req.published_doi)
        if preprint is None or published is None:
            log.warning(
                "pipeline aborted: missing doc in ES (preprint=%s found=%s, published=%s found=%s)",
                req.preprint_doi, preprint is not None,
                req.published_doi, published is not None,
            )
            return

        log.info(
            "fetched preprint v%s + published v%s for pair preprint=%s",
            preprint.get("version"), published.get("version"), req.preprint_doi,
        )

        # Step 3: build envelope, stream supervisor events. Each raw ADK event
        # is also translated to a §6.1 envelope and written to `agent_events`
        # in real time so the BFF's SSE handler can tail the stream — this is
        # the production replacement for the mock SSE channel (contracts.md §6.3).
        # We don't know `drift_event_id` until drift_analyzer completes mid-stream,
        # so envelopes are written with drift_event_id=null and the IDs are
        # back-filled via update_by_query at the end of run_pipeline.
        dispatch_id = str(uuid.uuid4())
        envelope = build_envelope(preprint, published)
        events: list[dict[str, Any]] = []
        translator_state = TranslatorState()
        agent_event_seq = 0
        agent_event_rows: list[tuple[str, dict[str, Any]]] = []
        async for event in get_supervisor_stream(envelope, user_id=f"dispatcher::{req.preprint_doi}"):
            events.append(event)
            # Translate + buffer; flush in small batches so a slow consumer
            # doesn't bottleneck the stream and ES doesn't see N tiny writes.
            for envelope_doc in translate_adk_event(event, translator_state, drift_event_id=None):
                agent_event_seq += 1
                envelope_doc["dispatch_id"] = dispatch_id
                envelope_doc["event_seq"] = agent_event_seq
                envelope_doc["preprint_doi"] = req.preprint_doi
                envelope_doc["published_doi"] = req.published_doi
                envelope_doc["record_source"] = "dispatcher"
                doc_id = f"{dispatch_id}::{agent_event_seq}"
                agent_event_rows.append((doc_id, envelope_doc))
            if len(agent_event_rows) >= 4:
                await bulk_index("agent_events", agent_event_rows)
                agent_event_rows = []
        if agent_event_rows:
            await bulk_index("agent_events", agent_event_rows)
        # Force a refresh on the agent_events index so the subsequent
        # update_by_query (back-fill of drift_event_id) sees the rows we just
        # wrote. Without this, the index's 5s refresh_interval (the Elastic
        # Serverless minimum, see contracts.md §2.2.8) leaves the last batch
        # of envelopes invisible to update_by_query and they keep
        # drift_event_id=null — frontend would then fail to tail them when
        # querying by drift_event_id post-hoc.
        try:
            await get_es().indices.refresh(index="agent_events")
        except Exception:
            log.exception("agent_events refresh failed (non-fatal; back-fill may miss tail rows)")
        log.info(
            "supervisor stream complete: %d ADK events -> %d agent_events rows (dispatch_id=%s)",
            len(events), agent_event_seq, dispatch_id,
        )

        # Step 4: parse stream into 4 structured outputs.
        # Prefer the supervisor's republished drift_event over drift_analyzer's raw
        # output: supervisor mints event_id immediately after drift_analyzer returns
        # (agents/supervisor_agent/agent.py post-Bug-4 fix) and re-yields a final
        # author=supervisor_agent event carrying the COMPLETE drift_event JSON with
        # that event_id baked in. Using this as the source of truth keeps
        # drift_events._id consistent with the drift_event_id supervisor passed to
        # citation_finder / notifier fan-outs (T1 round-3 finding 2026-05-26).
        drift_event = extract_final_output(events, "supervisor_agent")
        if drift_event is None:
            # Older supervisor build without the republish step — fall back to
            # drift_analyzer's raw output and dispatcher-side UUID mint (legacy path).
            log.warning("no supervisor_agent final event; falling back to drift_analyzer parse")
            drift_event = extract_final_output(events, "drift_analyzer")
        citation_result = extract_final_output(events, "citation_finder")
        notifications = extract_notifier_outputs(events)
        # memory_synthesizer output is logged but not persisted (it self-writes via MCP).

        if drift_event is None:
            log.warning("pipeline aborted: drift_analyzer produced no parseable output")
            return
        affected_citations = (citation_result or {}).get("affected_citations") or []

        log.info(
            "parsed stream: 1 drift_event + %d affected_citations + %d notifications",
            len(affected_citations), len(notifications),
        )
        log.info(
            "  drift_event.materiality_score=%s drift_event.claim_diffs=%d retrieved_patterns=%s",
            drift_event.get("materiality_score"),
            len(drift_event.get("claim_diffs") or []),
            drift_event.get("retrieved_patterns_used"),
        )

        # Step 5: ES bulk-write
        now = datetime.now(timezone.utc).isoformat()
        drift_event_id = drift_event.get("event_id") or str(uuid.uuid4())
        drift_event["event_id"] = drift_event_id
        drift_event["analyzed_at"] = now
        drift_event["detected_at"] = now

        await index_doc("drift_events", drift_event_id, drift_event)
        log.info("wrote drift_events/_doc/%s", drift_event_id)

        # Back-fill drift_event_id on the agent_events rows we wrote with null
        # while streaming (we didn't know the id until drift_analyzer minted it
        # mid-stream). Frontend tails agent_events by drift_event_id, so this
        # update is what makes "view history of dispatch X" work after the fact.
        try:
            # elasticsearch-py 8.x: keyword args, not `body=`
            await get_es().update_by_query(
                index="agent_events",
                refresh=True,
                conflicts="proceed",
                script={
                    "source": "ctx._source.drift_event_id = params.id",
                    "params": {"id": drift_event_id},
                },
                query={"term": {"dispatch_id": dispatch_id}},
            )
        except Exception:
            log.exception("agent_events drift_event_id back-fill failed for dispatch_id=%s", dispatch_id)

        # affected_citations: N rows (N=0 in the demo stub case)
        ac_rows: list[tuple[str, dict[str, Any]]] = []
        for ac in affected_citations:
            citing_doi = ac.get("citing_paper_doi")
            if not citing_doi:
                log.warning("affected_citation missing citing_paper_doi; skipping: %s", ac)
                continue
            ac_id = f"{drift_event_id}::{citing_doi}"
            ac["affected_citation_id"] = ac_id
            ac["drift_event_id"] = drift_event_id
            ac["scored_at"] = now
            ac_rows.append((ac_id, ac))

        # notification_log: 1 row per notification draft, keyed by affected_citation_id.
        # Build an index of recipient emails so we can hydrate notification_log.recipient_email
        # from the upstream citation's first author (notifier output omits recipient echo).
        recipient_by_ac_id: dict[str, str | None] = {}
        for ac in affected_citations:
            ac_id = ac.get("affected_citation_id")
            if not ac_id:
                continue
            authors = ac.get("citing_paper_authors") or []
            recipient_by_ac_id[ac_id] = (authors[0].get("email") if authors else None)

        nl_rows: list[tuple[str, dict[str, Any]]] = []
        for note in notifications:
            ac_id = note.get("affected_citation_id")
            if not ac_id:
                log.warning("notification missing affected_citation_id; skipping: %s", note)
                continue
            nl_rows.append((ac_id, sanitize_notification(note, drift_event_id, recipient_by_ac_id.get(ac_id), now)))

        if ac_rows:
            await bulk_index("affected_citations", ac_rows)
            log.info("wrote affected_citations: %d rows", len(ac_rows))
        if nl_rows:
            await bulk_index("notification_log", nl_rows)
            log.info("wrote notification_log: %d rows (status=drafted)", len(nl_rows))

        # Step 6: send each drafted notification + flip status in notification_log
        for ac_id, doc in nl_rows:
            await send_and_update(ac_id, doc)
        # TODO Step 5: ES bulk-write drift_events / affected_citations / notification_log
        # TODO Step 6: for each notification: Gmail send + notification_log status update
    except Exception:
        log.exception("pipeline failed for preprint=%s", req.preprint_doi)


# --- Pub/Sub publisher ------------------------------------------------------

_publisher: pubsub_v1.PublisherClient | None = None


def get_publisher() -> pubsub_v1.PublisherClient:
    """Lazily build a single Pub/Sub PublisherClient per process.

    The grpc client is heavy to construct (channel + auth + DNS); reuse it
    across all /dispatch calls. Thread-safe by design (the underlying grpc
    channel is). One client per process is the recommended pattern in the
    google-cloud-pubsub README.
    """
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


# --- ES client --------------------------------------------------------------

_es: AsyncElasticsearch | None = None


def get_es() -> AsyncElasticsearch:
    """Lazily build a single AsyncElasticsearch client per process."""
    global _es
    if _es is None:
        _es = AsyncElasticsearch(
            hosts=[ELASTIC_ENDPOINT],
            api_key=ELASTIC_API_KEY,
            request_timeout=30,
        )
    return _es


# --- Stream parsing ---------------------------------------------------------


def _strip_markdown_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _extract_from_events(events: list[dict[str, Any]]) -> dict | None:
    """Find the agent's final §3.x.2 output, which lives in a text part as a
    markdown-fenced JSON blob. Walk events in reverse, concatenate text parts
    (Gemini sometimes splits one JSON across multiple), strip fence, parse.

    We deliberately ignore function_response parts: in the raw event dict, MCP
    tool returns are wrapped as {"content": [...], "isError": bool} which is
    NOT the agent's business output. The agent's true final answer is always
    a subsequent text part.
    """
    for event in reversed(events):
        parts = ((event.get("content") or {}).get("parts")) or []
        combined = "".join(p.get("text", "") for p in parts if "text" in p)
        if not combined.strip():
            continue
        stripped = _strip_markdown_fence(combined)
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def extract_final_output(events: list[dict[str, Any]], author: str) -> dict | None:
    """Extract the final structured output for a given sub-agent."""
    agent_events = [e for e in events if e.get("author") == author]
    return _extract_from_events(agent_events)


def extract_notifier_outputs(events: list[dict[str, Any]]) -> list[dict]:
    """Notifier is invoked once per affected citation; group by invocation_id.

    Returns one §3.4.2 envelope per notifier invocation. Empty list when N=0
    (the demo HCQ stub case — no OpenAlex citing works).
    """
    by_invocation: dict[str, list[dict]] = {}
    for ev in events:
        if ev.get("author") != "notifier":
            continue
        inv_id = ev.get("invocation_id") or ""
        by_invocation.setdefault(inv_id, []).append(ev)

    notifications: list[dict] = []
    for inv_id, group in by_invocation.items():
        out = _extract_from_events(group)
        if out is None:
            log.warning("notifier invocation_id=%s produced no parseable output; skipping", inv_id)
            continue
        notifications.append(out)
    return notifications


# --- Supervisor stream ------------------------------------------------------

_supervisor: Any | None = None


def get_supervisor() -> Any:
    """Lazily init vertexai + resolve the deployed supervisor reasoning engine."""
    global _supervisor
    if _supervisor is None:
        vertexai.init(project=GCP_PROJECT, location=GCP_REGION)
        _supervisor = agent_engines.get(SUPERVISOR_REASONING_ENGINE_ID)
    return _supervisor


def build_envelope(preprint: dict[str, Any], published: dict[str, Any]) -> dict[str, Any]:
    """Pluck the 5 fields the supervisor expects per side (see agents/supervisor_agent/agent.py:44)."""
    def pick(doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "doi": doc.get("doi"),
            "version": doc.get("version"),
            "title": doc.get("title"),
            "abstract": doc.get("abstract"),
            "conclusion": doc.get("conclusion"),
        }
    return {"preprint": pick(preprint), "published": pick(published)}


async def get_supervisor_stream(
    envelope: dict[str, Any], *, user_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Yield supervisor events. Live mode hits Agent Engine; stub mode replays JSON."""
    if USE_STUB_STREAM:
        log.info("USE_STUB_STREAM=1 -> replaying %s", STUB_STREAM_PATH.name)
        stub_events = json.loads(STUB_STREAM_PATH.read_text(encoding="utf-8"))
        for ev in stub_events:
            yield ev
        return

    message = json.dumps(envelope, ensure_ascii=False)
    async for event in get_supervisor().async_stream_query(message=message, user_id=user_id):
        yield event


# --- Gmail send -------------------------------------------------------------

_gmail_service: Any | None = None


def get_gmail_service() -> Any:
    """Lazily load OAuth secrets from Secret Manager and build a Gmail v1 service."""
    global _gmail_service
    if _gmail_service is not None:
        return _gmail_service

    sm = secretmanager.SecretManagerServiceClient()

    def fetch(secret_name: str) -> str:
        path = f"projects/{GCP_PROJECT}/secrets/{secret_name}/versions/latest"
        return sm.access_secret_version(name=path).payload.data.decode("utf-8")

    refresh_token = fetch(GMAIL_SECRETS["refresh_token"])
    client_id = fetch(GMAIL_SECRETS["client_id"])
    client_secret = fetch(GMAIL_SECRETS["client_secret"])

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
    )
    _gmail_service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    log.info("Gmail service initialized (OAuth secrets loaded)")
    return _gmail_service


def _build_raw_message(to: str, subject: str, body: str) -> str:
    msg = MIMEText(body)
    msg["To"] = to
    msg["Subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _send_sync(to: str, subject: str, body: str) -> str:
    """Blocking Gmail send. Returns the Gmail message id."""
    raw = _build_raw_message(to, subject, body)
    resp = get_gmail_service().users().messages().send(
        userId="me", body={"raw": raw},
    ).execute()
    return resp.get("id", "")


async def send_and_update(ac_id: str, doc: dict[str, Any]) -> None:
    """Send the drafted notification via Gmail; update notification_log status."""
    recipient = doc.get("recipient_email") or DEMO_FALLBACK_EMAIL
    if not recipient:
        log.warning(
            "skipping send for %s: no recipient_email (citation_finder v0 limitation; "
            "set DEMO_FALLBACK_EMAIL to route to a demo inbox)", ac_id,
        )
        await get_es().update(
            index="notification_log", id=ac_id,
            doc={"status": "skipped", "error_message": "no recipient email"},
            refresh=False,
        )
        return

    subject = doc.get("subject") or "(no subject)"
    body = doc.get("body") or ""
    try:
        msg_id = await asyncio.to_thread(_send_sync, recipient, subject, body)
        sent_at = datetime.now(timezone.utc).isoformat()
        await get_es().update(
            index="notification_log", id=ac_id,
            doc={"status": "sent", "sent_at": sent_at, "recipient_email": recipient},
            refresh=False,
        )
        log.info("gmail sent to=%s msg_id=%s ac_id=%s", recipient, msg_id, ac_id)
    except HttpError as e:
        log.exception("gmail send failed for ac_id=%s", ac_id)
        await get_es().update(
            index="notification_log", id=ac_id,
            doc={"status": "failed", "error_message": f"{type(e).__name__}: {e}"[:500]},
            refresh=False,
        )
    except Exception as e:
        log.exception("unexpected error sending mail for ac_id=%s", ac_id)
        await get_es().update(
            index="notification_log", id=ac_id,
            doc={"status": "failed", "error_message": f"{type(e).__name__}: {e}"[:500]},
            refresh=False,
        )


def sanitize_notification(
    note: dict[str, Any],
    drift_event_id: str,
    recipient_email: str | None,
    now_iso: str,
) -> dict[str, Any]:
    """Project the notifier's §3.4.2 output onto notification_log's strict schema.

    Drops the `dispatch` wrapper and any other unknown fields; promotes nested
    dispatch.* values to top-level slots written by the dispatcher (status,
    sent_at, error_message). reasoning_trace and drafted_at are kept; the
    recipient_email is hydrated from the parent affected_citations row.
    """
    return {
        "affected_citation_id": note.get("affected_citation_id"),
        "drift_event_id": drift_event_id,
        "recipient_email": recipient_email,
        "subject": note.get("subject"),
        "body": note.get("body"),
        "reasoning_trace": note.get("reasoning_trace"),
        "status": "drafted",
        "drafted_at": now_iso,
        "sent_at": None,
        "error_message": None,
    }


async def index_doc(index_name: str, doc_id: str, doc: dict[str, Any]) -> None:
    """Index a single document with explicit _id. Used for the drift_event parent
    row (which must succeed before children are written)."""
    await get_es().index(index=index_name, id=doc_id, document=doc, refresh=False)


async def bulk_index(index_name: str, rows: list[tuple[str, dict[str, Any]]]) -> None:
    """Bulk index rows of (doc_id, doc). Raises on any item-level failure."""
    actions = (
        {"_op_type": "index", "_index": index_name, "_id": doc_id, "_source": doc}
        for doc_id, doc in rows
    )
    success, errors = await async_bulk(get_es(), actions, refresh=False, raise_on_error=True)
    if errors:
        log.error("bulk_index %s reported errors: %s", index_name, errors[:3])


AGENT_EVENTS_RETENTION_DAYS = 30


async def sweep_agent_events_retention() -> None:
    """Delete agent_events rows older than the retention window.

    Elastic Serverless does not expose the classic ILM API, so retention is
    enforced application-side. Cheap to run (range query on `timestamp`),
    safe to invoke on every dispatch — we run it fire-and-forget so a slow
    sweep doesn't block the pipeline.
    """
    try:
        # elasticsearch-py 8.x: keyword args, not `body=`
        await get_es().delete_by_query(
            index="agent_events",
            conflicts="proceed",
            query={"range": {"timestamp": {"lt": f"now-{AGENT_EVENTS_RETENTION_DAYS}d/d"}}},
        )
    except Exception:
        log.exception("agent_events retention sweep failed (non-fatal)")


async def fetch_preprint(doi: str) -> dict[str, Any] | None:
    """Search preprints by DOI. Returns the best matching _source, or None.

    The `preprints` index uses composite ids `{normalized_doi}::{version}` (see
    ingestion.common.doi.preprint_id), so we cannot GET by raw DOI. We search
    by `doi` term and prefer the final-preprint version (or `published`) when
    multiple versions exist.
    """
    resp = await get_es().search(
        index="preprints",
        size=1,
        query={"term": {"doi": doi}},
        sort=[{"is_final_preprint": "desc"}, {"posted_date": "desc"}],
    )
    hits = resp["hits"]["hits"]
    if not hits:
        log.warning("preprints doi=%s not found", doi)
        return None
    return dict(hits[0]["_source"])
