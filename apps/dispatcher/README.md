# claimdrift-dispatcher

Cloud Run service that the scheduled Elastic Workflow calls to drive the
supervisor agent (deployed on Vertex AI Agent Engine), persist its outputs to
Elasticsearch, and send notification emails via Gmail.

Owns the outer-loop side of the §9.6.1 inverted topology in
[../../docs/contracts.md](../../docs/contracts.md):

```
Elastic Scheduled Workflow → POST /dispatch (this service)
  ↓  bearer auth + idempotency check
publish to Pub/Sub topic `claimdrift-dispatch` → return 202 in ~100ms
  ↓  push subscription (Google-signed OIDC)
POST /run (this service)
  ↓
ES preprints reverse-lookup
  ↓
supervisor.async_stream_query (Vertex AI Agent Engine)
  ↓
parse stream → write drift_events / affected_citations / notification_log
  ↓
Gmail send per notification → flip notification_log.status
```

The pipeline runs through Pub/Sub because Elastic Workflows' `http` connector
has a platform-fixed ~60s timeout on Serverless; `/dispatch` must return well
inside that, while `/run` needs the full ~200s pipeline budget (the
subscription's `ack-deadline=600s`). See contracts §9.6.1 changelog 2026-05-28
for the full rationale and the fire-and-forget approaches it replaced.

The supervisor itself orchestrates the 5 sub-agents on Agent Engine; this
service is the §4.1 trigger + persistence layer. See
[main.py](main.py) for the §9.6.1 dispatcher contract.

## Endpoints

- `POST /dispatch` — `Authorization: Bearer <WF_BEARER_TOKEN>`, body
  `{"preprint_doi": "...", "published_doi": "..."}`. Bearer-auths,
  idempotency-checks against `drift_events`, then publishes the pair to the
  `claimdrift-dispatch` Pub/Sub topic and returns `{"status": "enqueued",
  "message_id": ...}` in ~100ms. Returns `{"status": "already_processed",
  "drift_event_id": ...}` if the pair already has a drift_event (add
  `?force=true` to re-run anyway).
- `POST /run` — the Pub/Sub **push-subscription** target, not called directly.
  Verifies the Google-signed OIDC token (`email` == push SA, `aud` == this
  endpoint URL), then `await`s the full ~200s pipeline before returning 200.
  Awaiting (rather than fire-and-forget) is what keeps the Cloud Run instance
  visibly busy so the autoscaler scales out — see "Runtime sizing" below.
- `GET /health` — unauthenticated health probe. (Do NOT rename to `/healthz` or
  any `/_ah/*` path — those are GFE-reserved and Cloud Run intercepts them
  before the request reaches the container.)

## Local development

```bash
cp .env.example .env  # then fill in real values; see § Configuration below
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

`/dispatch` always publishes to the real `claimdrift-dispatch` Pub/Sub topic
(there is no local Pub/Sub bypass), so a local smoke test needs `GCP_PROJECT`
set + ADC (`gcloud auth application-default login`) and the topic created:

```bash
curl -X POST http://localhost:8080/dispatch \
  -H "Authorization: Bearer $(grep WF_BEARER_TOKEN .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"preprint_doi": "10.1101/2024.05.03.24306688", "published_doi": "10.1007/978-3-031-66535-6_19"}'
# Expect: {"status": "enqueued", "message_id": "..."} in <200ms (or
# {"status": "already_processed", ...} if this pair was processed before).
```

`/run` cannot be exercised locally with a raw curl — it verifies a real
Google-signed Pub/Sub OIDC token and has no bypass. To exercise the **pipeline**
itself offline (no Pub/Sub, no OIDC, no live reasoning engine), replay the golden
stream straight into the writer with `USE_STUB_STREAM=1`:

```bash
USE_STUB_STREAM=1 python scripts/replay_supervisor_stream.py \
  --preprint-doi 10.1101/2024.05.03.24306688 \
  --published-doi 10.1007/978-3-031-66535-6_19
```

The reference golden pair above is the T1 real-N>0 baseline; see
[tests/golden/](tests/golden/) for the captured outputs and
[scripts/replay_supervisor_stream.py](scripts/replay_supervisor_stream.py) for
flags.

## Configuration

Required env vars (`.env` locally, `--set-env-vars` / `--set-secrets` on
Cloud Run):

| Var | Source | Notes |
|---|---|---|
| `GCP_PROJECT` | env | GCP project hosting Agent Engine + Secret Manager |
| `GCP_REGION` | env | Agent Engine region (default `us-central1`) |
| `SUPERVISOR_REASONING_ENGINE_ID` | env | Deployed supervisor numeric id |
| `ELASTIC_ENDPOINT` | env | `https://*.es.<region>.gcp.cloud.es.io` — NOT the Kibana URL |
| `ELASTIC_API_KEY` | secret | base64 API key; on Cloud Run mount via Secret Manager `elastic-api-key:latest` (verify name with `gcloud secrets list`) |
| `WF_BEARER_TOKEN` | secret | random 32+ char token; the Workflow YAML carries the matching value |
| `PUBSUB_TOPIC` | env | topic `/dispatch` publishes to; default `claimdrift-dispatch` |
| `PUBSUB_PUSH_SA_EMAIL` | env | SA email `/run` requires in the OIDC `email` claim; must match the subscription's `--push-auth-service-account`. Default is the project Compute SA (`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`). |
| `PUBSUB_PUSH_AUDIENCE` | env | expected OIDC `aud` claim; set to `<DISPATCHER_URL>/run` so it matches the subscription's `--push-auth-token-audience` (request-URL reconstruction is unreliable behind the Cloud Run proxy). |
| `USE_STUB_STREAM` | env | unset in prod; `1` locally to replay `_stub_stream.json` instead of the live reasoning engine |
| `DEMO_FALLBACK_EMAIL` | env | recipient when notifier's `recipient_email` is null (§3.3 v0 limitation — citing_paper_authors[].email is always null). Leave unset in production. |

Gmail OAuth credentials (`gmail-refresh-token`, `gmail-oauth-client-id`,
`gmail-oauth-client-secret`) are read at first send from Secret Manager —
not env vars. See `GMAIL_SECRETS` in [main.py](main.py).

**One-time Gmail OAuth setup.** Before the first real send, create those three
secrets with [scripts/gmail_oauth_setup.py](scripts/gmail_oauth_setup.py).
Prereqs: Gmail API enabled on the project, a **Desktop-app** OAuth client
downloaded to `apps/dispatcher/scripts/client_secret.json`, and
`gcloud auth application-default login`. Then:

```bash
cd apps/dispatcher/scripts
uv run python gmail_oauth_setup.py   # opens a browser; sign in as the notifier inbox
```

It runs the consent flow and writes the refresh token + client id/secret to
Secret Manager. Re-run before each demo/judging window: an External + Testing
OAuth app's refresh token expires after 7 days.

## Deploy

The dispatcher imports the §6.1 SSE envelope translator from `apps/bff/sse_adapter.py`
(shared with the BFF — single owner of the envelope shape). The build context must
therefore be the **repo root**, not `apps/dispatcher/`; the `Dockerfile` here uses
repo-root-relative `COPY` paths accordingly.

`gcloud run deploy --source` does not support a `--dockerfile` flag pointing at a
Dockerfile in a subdirectory, so deploy is a two-step Cloud Build + Cloud Run:

From the **repo root**:

```bash
# 1. Build the image with the dispatcher's Dockerfile (uses repo root as context).
gcloud builds submit . --config=apps/dispatcher/cloudbuild.yaml

# 2. Deploy the freshly built image to Cloud Run.
#    The runtime flags below are mandatory; see "Runtime sizing" below.
#    --timeout=600 because /run awaits the full ~200s pipeline (default 300s
#    would be tight under retries; the Pub/Sub ack-deadline is also 600s).
gcloud run deploy claimdrift-dispatcher \
  --image=us-central1-docker.pkg.dev/tensile-topic-496519-i1/cloud-run-source-deploy/claimdrift-dispatcher:latest \
  --region=us-central1 \
  --project=tensile-topic-496519-i1 \
  --allow-unauthenticated \
  --timeout=600 \
  --min-instances=1 --max-instances=20 --concurrency=1 --cpu-boost \
  --update-env-vars="GCP_PROJECT=tensile-topic-496519-i1,GCP_REGION=us-central1,SUPERVISOR_REASONING_ENGINE_ID=<id>,ELASTIC_ENDPOINT=<url>,DEMO_FALLBACK_EMAIL=<addr>,PUBSUB_PUSH_AUDIENCE=<dispatcher-url>/run" \
  --set-secrets="WF_BEARER_TOKEN=wf-bearer-token:latest,ELASTIC_API_KEY=elastic-api-key:latest"
```

The Pub/Sub topic + push subscription that feed `/run` are a one-time setup
separate from the service deploy — see the root README's "Reproduce" step 5b
for the `gcloud pubsub topics/subscriptions create` + IAM commands.

### Runtime sizing: `--concurrency=1` + `--max-instances=20` + `--cpu-boost` + `--min-instances=1`

The pipeline runs in `/run`, which **`await`s the full ~200s pipeline** before
returning 200 (it does NOT fire-and-forget). This is deliberate: an earlier
design returned 202 immediately and ran the pipeline in `asyncio.create_task`,
but Cloud Run's autoscaler only counts in-flight HTTP requests, so a handler
that returned in 100ms made every instance look idle — the service never scaled
past one instance and a single event loop saturated under the background tasks.
Awaiting keeps each instance visibly busy, so concurrent Pub/Sub pushes scale
the service out to `--max-instances`. (`/dispatch` still returns in ~100ms, but
all it does is publish to Pub/Sub — there is no background pipeline behind it.)

The flags together produce the desired isolation for `/run`:

| Flag | Why |
|---|---|
| `--concurrency=1` | One instance handles one `/run` pipeline at a time. No event-loop contention between concurrent pipelines. |
| `--max-instances=20` | A single 5-min workflow tick enqueues up to 20 pairs (`search_new_pairs.size = 20`); Pub/Sub pushes fan out, each landing on its own instance. 20 is also a hard ceiling so the backlog can't spiral. |
| `--cpu-boost` | Doubles CPU for 5s on container start. Brings cold-start of the vertexai SDK + Agent Engine reasoning engine resolve from ~40s down to ~10-20s. |
| `--min-instances=1` | Keeps one instance always warm so the first `/dispatch` after an idle period stays well inside the workflow's 60s connector timeout. |
| `--timeout=600` | `/run` awaits ~200s; the 300s default would kill long pipelines under retries. Pairs with the subscription's `ack-deadline=600s`. |

All live as of 2026-05-28. Costs are modest: one always-on
Cloud Run instance + up to 20 short-lived instances during workflow
fan-out (each lives ~3-5 minutes). No GPU.

The Artifact Registry path `us-central1-docker.pkg.dev/<project>/cloud-run-source-deploy/<service>`
is the default location `gcloud run deploy --source` writes to on first use; the
`cloud-run-source-deploy` repo is auto-created the first time you deploy. If you
already have an existing repo by another name, update both lines accordingly.

`--allow-unauthenticated` is intentional: the Elastic Workflow `http.request`
step calls `/dispatch` with only a bearer header, no GCP IAM identity, so the
service can't gate on Cloud Run IAM at the platform level. Auth is enforced
in-handler instead: `/dispatch` checks the bearer token; `/run` verifies the
Pub/Sub-signed OIDC token (`email` == push SA, `aud` == the `/run` URL) so only
our own subscription can trigger a pipeline despite `allUsers` invoke access.

The default Compute Engine service account on Cloud Run needs four IAM roles
that are NOT granted by Editor in newer GCP projects:
`roles/cloudbuild.builds.builder`, `roles/logging.logWriter`,
`roles/secretmanager.secretAccessor`, `roles/aiplatform.user`. See
[../../docs/contracts.md](../../docs/contracts.md) changelog 2026-05-25 for
the full Cloud Run deploy gotcha list.

## Golden tests

[tests/golden/](tests/golden/) holds the T1 reproducible run: one real
`(preprint, published)` pair through the full pipeline, with
`drift_event`, `affected_citations`, and `notification_log` snapshots that
all share a single drift_event UUID prefix — the cross-table join sanity
check from §2.2.3-6.

[scripts/](scripts/) holds spike + analysis tools that produced the golden
dump (`replay_supervisor_stream.py`, `analyze_stream.py`, `verify_fix.py`)
plus one-off diagnostic readers. Re-runnable; the only live side effect is
one extra `memory_synthesizer` write to `drift_patterns`.
