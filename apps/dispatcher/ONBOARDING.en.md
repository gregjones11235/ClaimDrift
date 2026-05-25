# Track 2 onboarding — Cloud Run dispatcher service

> Audience: A (joining the project to take Phase 5f Track 2).
> Author: C (Jiayu) — ping me on chat for anything unclear.
> Status: this doc + `.gitignore` + `_stub_stream.json` (real supervisor event capture from C) are in place. Everything else you build from zero.

---

## 1. Where this service sits in the system

```
Elastic Workflow (5min scheduled)
    │  POST /dispatch  { "published_doi": "...", "preprint_doi": "..." }
    │  Authorization: Bearer <static token in WF YAML>
    ▼
[Cloud Run "dispatcher"]   ← YOUR SCOPE
    │ (a) bearer auth
    │ (b) ES read: full preprint + published docs for those two DOIs
    │ (c) GCP access token from own service-account identity
    │ (d) call supervisor_agent on Vertex AI Agent Engine:
    │       POST .../reasoningEngines/<SUPERVISOR_ID>:streamQuery
    │     parse the stream → collect
    │       1× drift_event, N× affected_citations, N× notification drafts
    │ (e) ES bulk-write: drift_events, affected_citations, notification_log (status=drafted)
    │ (f) for each notification draft: Gmail API send → update notification_log status=sent
    ▼
HTTP 202 Accepted (fire-and-forget; whole pipeline can take minutes)
```

Everything inside the dashed box is yours. C owns:
- the supervisor agent on the other side of step (d) — exposed as a stable HTTP endpoint
- the Elastic Workflow YAML on the other side of step (a) — sends a fixed payload shape

Your contract with C is: **input shape (a) + output behaviour (e)+(f)**. You can implement (b)–(f) however you want as long as the side effects on ES + Gmail match the schema.

> **Supervisor internals (informational, you don't need to act on this)**: The supervisor is a custom ADK `BaseAgent` that already does the §4.1 fan-out internally — `claim_extractor` ×2 in parallel → `drift_analyzer` → `citation_finder` → `notifier` ×N (one per affected citation, in parallel). You consume the merged event stream as a single flat sequence. Your job in step (d) is just to identify which sub-agent each event came from and route the final outputs accordingly. You do NOT need to invoke `notifier` per-citation yourself — supervisor already did.

---

## 2. Required reading (in this order, ~30 min)

1. **`docs/contracts.md` §9.6.1** — orchestration topology and the rationale for why dispatcher exists at all (don't skip this, it explains why you're not just calling supervisor directly from the Workflow YAML).
2. **`docs/contracts.md` §2.2.3 / §2.2.4 / §2.2.6** — ES index schemas you'll bulk-write into (`drift_events`, `affected_citations`, `notification_log`).
3. **`docs/contracts.md` §2.2.1** — `preprints` index schema (what fields you SELECT in step (b)).
4. **`docs/contracts.md` §3.2.2 / §3.3.2 / §3.4.2** — supervisor sub-agents' output schemas — these are the JSON shapes you'll see inside the stream and persist in step (e).
5. **`docs/contracts.md` §2.3** — `record_source` field rule. **You do NOT set this field for anything you write** — leave it unset (real-data convention; demo seed adds it elsewhere).
6. **`agents/_DEPLOY_CHECKLIST.md`** — has the 5 sub-agent reasoning-engine IDs in the status table; gives you a feel for the deploy pattern (you're not deploying ADK agents, but the IDs in that table feed indirectly into the supervisor).

Skim only — don't try to absorb the whole file. The five sections above are load-bearing.

---

## 3. Inputs you need from C

| Item | Value / source | Status |
|---|---|---|
| `SUPERVISOR_REASONING_ENGINE_ID` | **`7816826734824652800`** | ✅ Deployed (`tensile-topic-496519-i1` / `us-central1`) |
| `_stub_stream.json` | `apps/dispatcher/_stub_stream.json` | ✅ Captured (full event stream from one real supervisor run; use this for `USE_STUB_STREAM` development) |
| `WF_BEARER_TOKEN` (static) | Agree on a value with C, write to Secret Manager | ⏳ Pick one (suggest you generate a random 32-char string and send to C) |
| `KIBANA_URL` + `ELASTIC_API_KEY` | `agents/.env` (in repo, gitignored) | ✅ Existing |
| GCP project + region | `tensile-topic-496519-i1`, `us-central1` | ✅ Public in codebase |
| Sub-agent reasoning engine IDs (reference only) | See status table in `agents/_DEPLOY_CHECKLIST.md` | Dispatcher doesn't call these directly; supervisor does. You may see them in trace events. |

You have **Project Editor** on the GCP project, so:
- You can `gcloud run deploy` directly without C provisioning a service account first — Cloud Run will use the default Compute Engine SA which already has the roles you need (`aiplatform.user`, `secretmanager.secretAccessor`). If you'd rather have a dedicated SA for cleanliness, you can `gcloud iam service-accounts create dispatcher-sa` yourself.
- You can read/write Secret Manager directly (creating the secret values for `WF_BEARER_TOKEN` etc.).
- You can deploy/redeploy Cloud Run as many times as you need without coordinating with C.
- You run the Gmail OAuth setup (Step 0 below) yourself and pick your own secret names.

`_stub_stream.json` is in place — you can `USE_STUB_STREAM=1` to drive Steps 4-6 development immediately. Step 8 E2E switches to the real supervisor.

---

## 4. Step-by-step tasks

Each step independently runnable.

### Step 0 — Gmail OAuth setup (~30 min, **do this first**)

The dispatcher will send mail via Gmail API. The personal Gmail account (`gregjones11235@gmail.com`, non-Workspace) requires OAuth2 user consent + a stored refresh token — domain-wide delegation is Workspace-only (confirmed).

Write a one-shot script at `apps/dispatcher/scripts/gmail_oauth_setup.py`:

1. In GCP Console create an OAuth client ([Console → APIs & Services → Credentials → Create Credentials → OAuth client ID → Desktop app](https://console.cloud.google.com/apis/credentials)). Download `client_secret.json`. **Do NOT commit it.**
2. Enable Gmail API ([Console → APIs & Services → Enable APIs → Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com)).
3. Script uses `google-auth-oauthlib`: `InstalledAppFlow.from_client_secrets_file(...).run_local_server(port=0)` — browser opens, you consent as `gregjones11235@gmail.com` for the `https://www.googleapis.com/auth/gmail.send` scope.
4. Take `credentials.refresh_token` and write the three values into Secret Manager (pick your own names; suggested: `gmail-refresh-token` / `gmail-oauth-client-id` / `gmail-oauth-client-secret`):

```python
from google.cloud import secretmanager
client = secretmanager.SecretManagerServiceClient()
for name, value in [
    ("gmail-refresh-token", creds.refresh_token),
    ("gmail-oauth-client-id", creds.client_id),
    ("gmail-oauth-client-secret", creds.client_secret),
]:
    parent = f"projects/{PROJECT_ID}"
    try:
        client.create_secret(parent=parent, secret_id=name,
                             secret={"replication": {"automatic": {}}})
    except Exception:
        pass  # already exists
    client.add_secret_version(parent=f"{parent}/secrets/{name}",
                              payload={"data": value.encode()})
```

Script doesn't need to write any file as long as it runs to completion. Dispatcher pulls the three values from Secret Manager at startup (see Step 6).

Reference: https://developers.google.com/workspace/gmail/api/auth/web-server

### Step 1 — Scaffold a FastAPI service (~20 min)

```
apps/dispatcher/
  main.py              # FastAPI app, single POST /dispatch endpoint
  requirements.txt     # fastapi, uvicorn, google-cloud-aiplatform, elasticsearch, google-api-python-client, google-auth, google-auth-oauthlib, google-cloud-secret-manager
  Dockerfile           # python:3.12-slim base, copy main.py + requirements, ENTRYPOINT uvicorn
  .env.example         # WF_BEARER_TOKEN, KIBANA_URL, ELASTIC_API_KEY, SUPERVISOR_REASONING_ENGINE_ID, GCP_PROJECT
  .gitignore           # .env, __pycache__, scripts/client_secret*.json
  scripts/             # one-shot scripts: gmail_oauth_setup.py
  README.md            # one-paragraph "what is this", links back to this onboarding doc
```

`main.py` skeleton (write the bodies as you go):

```python
import os
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI()

class DispatchRequest(BaseModel):
    published_doi: str
    preprint_doi: str

@app.post("/dispatch", status_code=202)
async def dispatch(req: DispatchRequest, authorization: str = Header(...)):
    # (a) verify bearer
    if authorization != f"Bearer {os.environ['WF_BEARER_TOKEN']}":
        raise HTTPException(401)
    # (b) ES read preprint + published full docs
    # (c) mint GCP access token (Cloud Run service-account ADC handles this — vertexai SDK uses it automatically)
    # (d) call supervisor stream_query, collect outputs
    # (e) ES bulk-write
    # (f) for each notification draft: Gmail send → ES update
    return {"status": "accepted"}
```

Local run: `uvicorn main:app --reload --port 8080`.

### Step 2 — ES reverse lookup (~30 min)

Use `elasticsearch-py` async client. Two GETs:

```python
# step (b)
preprint_doc = await es.get(index="preprints", id=req.preprint_doi)
published_doc = await es.get(index="preprints", id=req.published_doi)  # same index; published_doi is also a row
```

Pull `abstract`, `conclusion`, `title`, `version` out of each. These are the fields claim_extractor needs per §3.1.1.

Caveat: a preprint and its published version are **two separate rows in the same `preprints` index**, keyed by their respective DOIs. The link is the `published_doi` field on the preprint row pointing at the published row's `_id` (Jeremy's pullers populate this).

### Step 3 — Build supervisor input envelope + call streamQuery (~1h)

Supervisor takes a single envelope like:

```json
{
  "preprint": { "doi": "...", "version": "v3", "title": "...", "abstract": "...", "conclusion": "..." },
  "published": { "doi": "...", "version": "v1", "title": "...", "abstract": "...", "conclusion": "..." }
}
```

(C will finalize this exact shape when writing supervisor; sync on chat. The shape above is what C is targeting today.)

Call pattern (the **only** new GCP SDK thing in your service):

```python
import json
import vertexai
from vertexai import agent_engines

vertexai.init(project="tensile-topic-496519-i1", location="us-central1")

supervisor = agent_engines.get(os.environ["SUPERVISOR_REASONING_ENGINE_ID"])

# IMPORTANT: AdkApp.stream_query takes (message, user_id, session_id?, run_config?).
# `message` must be a str or an ADK Content dict — there's no `input=` kwarg.
# We JSON-serialize the envelope into the message string; supervisor (C's code)
# does json.loads on the user message to pull out preprint / published.
envelope_json = json.dumps(envelope)

events = []
async for event in supervisor.async_stream_query(
    message=envelope_json,
    user_id=f"dispatcher::{req.preprint_doi}",  # preprint_doi as user_id makes the trace easy to follow
):
    events.append(event)
```

`async_stream_query` is an async generator; the whole pipeline can take 30s–5min (supervisor internally chains 5 sub-agents). Since FastAPI handlers are async, you can either:
- `asyncio.create_task(run_pipeline(envelope))` fire-and-forget, handler returns 202 immediately
- or `await` the whole pipeline before returning (simpler but holds the HTTP connection for minutes; Cloud Run default 5-min timeout is fine, beyond that needs `--timeout=900`)

Recommended fire-and-forget pattern:

```python
import asyncio
asyncio.create_task(run_pipeline(envelope))
return {"status": "accepted"}
```

Each `event` in the stream is an ADK Event JSON dump (i.e. `Event.model_dump()`). Three main shapes:

```jsonc
// (i) sub-agent LLM thinking / emitting text:
{ "author": "claim_extractor", "content": { "parts": [{ "text": "..." }] } }

// (ii) sub-agent invoked an MCP tool (drift_analyzer / citation_finder / memory_synthesizer):
{ "author": "drift_analyzer", "content": { "parts": [{ "function_call": { "name": "search_drift_patterns", "args": {...} } }] } }

// (iii) MCP tool returned:
{ "author": "drift_analyzer", "content": { "parts": [{ "function_response": { "name": "search_drift_patterns", "response": {...} } }] } }
```

Every event carries `author` — the source sub-agent (`claim_extractor` / `drift_analyzer` / `citation_finder` / `notifier` / `memory_synthesizer`). **Use `event.author` to attribute events to sub-agents**, NOT `function_response.name` — the latter is the MCP tool name, not the sub-agent name.

### How to extract each sub-agent's final §3.x.2 structured output from the stream

**Two paths**, depending on whether the sub-agent uses MCP tools:

| Sub-agent | Has MCP tools? | Where the final output lives |
|---|---|---|
| `claim_extractor` | ❌ | The **last event** authored by that agent: `content.parts[*].text` — a JSON string, **commonly wrapped in a ` ```json ... ``` ` markdown code fence** (Gemini habit, ignores INSTRUCTION "JSON only"). Strip the fence before `json.loads`. |
| `notifier` | ❌ | Same as above. |
| `drift_analyzer` | ✅ search_drift_patterns | Same as above (agent uses text-part for the final §3.2.2 output even after calling tools). |
| `citation_finder` | ✅ openalex_citing_works | Same as above. |
| `memory_synthesizer` | ✅ three drift_patterns tools | Same as above. |

So **all 5 sub-agents share the same extraction logic**: find that agent's last text-part event, strip markdown fence, `json.loads`. The `function_response` events contain MCP tool results, not the sub-agent's final output.

Reference helper (C uses this exact pattern in supervisor; copy it):

```python
def strip_markdown_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()

def extract_final_output(events: list[dict], agent_name: str) -> dict | None:
    """Pull agent_name's final JSON output from events."""
    agent_events = [e for e in events if e.get("author") == agent_name]
    for ev in reversed(agent_events):
        parts = (ev.get("content") or {}).get("parts") or []
        combined = "".join(p.get("text", "") for p in parts if "text" in p)
        if not combined.strip():
            continue
        stripped = strip_markdown_fence(combined)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            continue
    return None
```

**Multiple invocations of the same sub-agent** (`claim_extractor` runs twice — preprint then published): the two runs' events arrive in order preprint→published. The helper above only grabs "the last one" and so drops preprint. To separate, group by `event.invocation_id` or by timestamp. For the dispatcher specifically, **the two claim_extractor intermediate outputs don't need to be persisted** (drift_analyzer's output is what business needs), so you can ignore them. Extract only these 4 final outputs:

- 1× `drift_analyzer` → write to `drift_events` index
- 1× `citation_finder` → write to `affected_citations` index (one row per `affected_citations[]` element)
- N× `notifier` → write to `notification_log` index + send email
- 1× `memory_synthesizer` → not persisted to ES (memory_synthesizer already wrote `drift_patterns` via MCP itself; this event is for demo/log/observability)

### Step 4 — Parse stream → collect 1 drift_event, N affected_citations, N notification drafts (~30 min)

Using the `extract_final_output(events, agent_name)` helper from Step 3:

```python
drift_event = extract_final_output(events, "drift_analyzer")  # §3.2.2
citation_result = extract_final_output(events, "citation_finder")  # §3.3.2
affected_citations = citation_result["affected_citations"]  # length N

# notifier runs N times — extract_final_output picks only the last,
# so group by invocation_id and extract each.
notifier_events = [e for e in events if e.get("author") == "notifier"]
# Group by invocation_id, extract per group → N notification drafts, each §3.4.2
notifications = []  # length N
```

**N=0 case** (the demo's synthetic HCQ DOI has no OpenAlex citations, so `affected_citations: []` and notifier never runs): `notifications` is an empty list; skip Step 6 email send but still write the drift_event. `_stub_stream.json` is this N=0 case — your code MUST handle it.

### Step 5 — ES bulk-write (~45 min)

For each of the three collections, use `elasticsearch.helpers.async_bulk`. Document IDs:
- `drift_events`: `event_id` (UUID minted by drift_analyzer; supervisor fills in if null)
- `affected_citations`: `{drift_event_id}::{citing_doi}` per §2.2.4
- `notification_log`: `affected_citation_id` per §2.2.6

**Fields to fill from dispatcher, not from agent output** (because §3.x leaves them null — see contracts §3.2.2 `analyzed_at`, §3.4.2 `drafted_at`, §3.5.2 `synthesized_at` notes):

| Field | Index | Value |
|---|---|---|
| `analyzed_at` | drift_events | `datetime.now(UTC).isoformat()` |
| `detected_at` | drift_events | same |
| `scored_at` | affected_citations | same |
| `drafted_at` | notification_log | same |
| `status` | notification_log | `"drafted"` (will become `"sent"` in step (f)) |
| `record_source` | all three | **DO NOT SET** — leave the field unset per §2.3 |

### Step 6 — Gmail send + status update (~45 min)

Step 0 wrote three values into Secret Manager (`gmail-refresh-token`, `gmail-oauth-client-id`, `gmail-oauth-client-secret`). Pull them at dispatcher startup:

```python
from google.cloud import secretmanager
client = secretmanager.SecretManagerServiceClient()

def fetch(name):
    path = f"projects/{PROJECT}/secrets/{name}/versions/latest"
    return client.access_secret_version(name=path).payload.data.decode()

refresh_token = fetch("gmail-refresh-token")
oauth_client_id = fetch("gmail-oauth-client-id")
oauth_client_secret = fetch("gmail-oauth-client-secret")
```

Send via `google-api-python-client`'s Gmail v1:

```python
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import base64
from email.mime.text import MIMEText

creds = Credentials(
    token=None, refresh_token=refresh_token,
    token_uri="https://oauth2.googleapis.com/token",
    client_id=oauth_client_id,
    client_secret=oauth_client_secret,
)
service = build("gmail", "v1", credentials=creds, cache_discovery=False)

msg = MIMEText(notification["body"])
msg["to"] = notification["recipient"]["email"]
msg["subject"] = notification["subject"]
raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

resp = service.users().messages().send(userId="me", body={"raw": raw}).execute()
```

On success: `es.update(index="notification_log", id=affected_citation_id, doc={"status": "sent", "sent_at": now_iso})`.

On failure (HTTP error from Gmail): `status: "failed"`, `error_message: <exception text>`.

**Personal Gmail daily cap**: 500 emails / 24h on the account (gregjones11235@gmail.com), enforced mailbox-side — not adjustable. For demo this is plenty (a single E2E run sends 3–8 emails). If you ever exceed during testing, you'll get HTTP 403 with `Daily user limit exceeded`. Just wait — no code change needed.

### Step 7 — Cloud Run deploy (~30 min)

From `apps/dispatcher/`:

```bash
gcloud run deploy claimdrift-dispatcher \
  --source . \
  --region=us-central1 \
  --project=tensile-topic-496519-i1 \
  --allow-unauthenticated \
  --set-env-vars="GCP_PROJECT=tensile-topic-496519-i1,KIBANA_URL=...,SUPERVISOR_REASONING_ENGINE_ID=..." \
  --set-secrets="WF_BEARER_TOKEN=wf-bearer:latest,ELASTIC_API_KEY=elastic-api-key:latest"
```

You have Editor role, so the default Compute Engine SA Cloud Run uses already has everything you need (`aiplatform.user`, `secretmanager.secretAccessor`). If you'd prefer a dedicated SA for hygiene, create one and add `--service-account=<your-sa>@tensile-topic-496519-i1.iam.gserviceaccount.com` — not required for v0.

**`--allow-unauthenticated`** is intentional: the Elastic Workflow's `http.request` step calls your endpoint with only a bearer header (no GCP IAM identity). Cloud Run IAM (`--no-allow-unauthenticated`) would block that call entirely. The bearer check inside your handler is the only auth layer — that's fine for v0. Defense-in-depth (Cloud Run IAM via a Workflow-side OIDC token) is post-v0 polish.

### Step 8 — End-to-end smoke test (joint with C, ~1h)

C drives this. You provide the deployed dispatcher URL. C seeds a demo preprint+published pair and posts manually to your `/dispatch` endpoint with the bearer. Watch:
- Cloud Run logs: see the 8 steps tick through
- ES: confirm `drift_events`, `affected_citations`, `notification_log` rows appear
- Gmail: confirm test inbox receives 3–8 emails

---

## 5. Stub stream (already in place)

`apps/dispatcher/_stub_stream.json` already exists (gitignored — won't be committed). It's a **real capture of one supervisor invocation by C**, against the HCQ demo envelope (the v3 preprint + published pair in `elastic/demo_seed/preprints.json`).

This capture is the **N=0 affected_citations case** (demo DOI has no OpenAlex citations → notifier fan-out skipped). This is exactly the edge case you MUST handle. For N>0 captures (once B's pullers are live and real preprints flow), C reruns `agents/supervisor_agent/scripts/capture_stream.py` to replace the file.

Toggle in `main.py`:

```python
import json
from pathlib import Path

if os.environ.get("USE_STUB_STREAM"):
    events = json.loads(Path(__file__).parent.joinpath("_stub_stream.json").read_text())
else:
    events = []
    async for chunk in supervisor.async_stream_query(message=envelope_json, user_id=user_id):
        events.append(chunk)
```

Event counts run 50-150 (depends on how Gemini chunks tokens); `_stub_stream.json` is ~97KB.

---

## 6. Things you do NOT need to worry about

- **Supervisor agent code** — C is writing it.
- **`scheduled` Elastic Workflow YAML** — C is writing it. C just needs your deployed dispatcher URL + the agreed bearer token at the end.
- **Pullers populating the `preprints` index** — B (Jeremy) is doing Cloud Run Job + Scheduler in parallel. Until B's pullers are live, smoke test uses `elastic/scripts/seed_demo_to_es.py`-seeded rows.
- **`§6.1` SSE adapter for the frontend** — not in 5f scope. Don't try to stream events to anyone — just consume the supervisor stream internally and write side-effects.

---

## 7. Definition of done

- [ ] Step 0: OAuth setup runs to completion; all three Gmail secrets readable from Secret Manager
- [ ] `apps/dispatcher/` has the files listed in Step 1 plus `scripts/gmail_oauth_setup.py`
- [ ] Local `uvicorn` run + `curl -H "Authorization: Bearer ..." -d '{...}' localhost:8080/dispatch` returns 202 within 100ms
- [ ] With `USE_STUB_STREAM=1`, full pipeline runs against the stub: rows appear in ES, fake emails fire to Gmail
- [ ] `gcloud run deploy` succeeds; service URL returned
- [ ] C provides real `SUPERVISOR_REASONING_ENGINE_ID`; you set the env var and remove `USE_STUB_STREAM`
- [ ] E2E smoke test (Step 8) succeeds

Ping C on chat when you hit any blocker. Don't grind for >30 min on something that smells like a missing C-side input.
