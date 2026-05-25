"""ClaimDrift dispatcher service.

POST /dispatch is invoked by the scheduled Elastic Workflow with a pair of DOIs.
The handler authenticates via bearer token, then fires off the full pipeline
(ES reverse-lookup -> supervisor stream -> ES bulk-write -> Gmail send) in the
background and returns 202 immediately.

See apps/dispatcher/ONBOARDING.md for the full design.
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
from fastapi import FastAPI, Header, HTTPException
from google.cloud import secretmanager
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pydantic import BaseModel
from vertexai import agent_engines

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


@app.post("/dispatch", status_code=202)
async def dispatch(req: DispatchRequest, authorization: str = Header(...)) -> dict:
    # (a) Bearer auth
    if authorization != f"Bearer {WF_BEARER_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid bearer token")

    log.info(
        "dispatch accepted: preprint=%s published=%s stub=%s",
        req.preprint_doi, req.published_doi, USE_STUB_STREAM,
    )

    # Fire-and-forget the pipeline; handler returns 202 immediately.
    # The pipeline writes side effects to ES + Gmail; failures are logged but
    # cannot be returned to the Workflow caller (acceptable per design).
    asyncio.create_task(run_pipeline(req))

    return {"status": "accepted"}


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

        # Step 3: build envelope, stream supervisor events
        envelope = build_envelope(preprint, published)
        events: list[dict[str, Any]] = []
        async for event in get_supervisor_stream(envelope, user_id=f"dispatcher::{req.preprint_doi}"):
            events.append(event)
        log.info("supervisor stream complete: %d events for preprint=%s", len(events), req.preprint_doi)

        # Step 4: parse stream into 4 structured outputs
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
