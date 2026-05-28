# claimdrift-dispatcher

Cloud Run service that the scheduled Elastic Workflow calls to drive the
supervisor agent (deployed on Vertex AI Agent Engine), persist its outputs to
Elasticsearch, and send notification emails via Gmail.

Owns the outer-loop side of the §9.6.1 inverted topology in
[../../docs/contracts.md](../../docs/contracts.md):

```
Elastic Scheduled Workflow → POST /dispatch (this service)
  ↓
ES preprints reverse-lookup
  ↓
supervisor.async_stream_query (Vertex AI Agent Engine)
  ↓
parse stream → write drift_events / affected_citations / notification_log
  ↓
Gmail send per notification → flip notification_log.status
```

The supervisor itself orchestrates the 5 sub-agents on Agent Engine; this
service is the §4.1 trigger + persistence layer. See
[main.py](main.py) for the §9.6.1 dispatcher contract.

## Endpoints

- `POST /dispatch` — `Authorization: Bearer <WF_BEARER_TOKEN>`, body
  `{"preprint_doi": "...", "published_doi": "..."}`. Returns 202 immediately;
  the pipeline runs as a background task.
- `GET /health` — unauthenticated health probe. (Do NOT rename to `/healthz` or
  any `/_ah/*` path — those are GFE-reserved and Cloud Run intercepts them
  before the request reaches the container.)

## Local development

```bash
cp .env.example .env  # then fill in real values; see § Configuration below
pip install -r requirements.txt
uvicorn main:app --reload --port 8080

# Smoke test (in another terminal). Use USE_STUB_STREAM=1 in .env to replay
# _stub_stream.json instead of hitting the live reasoning engine.
curl -X POST http://localhost:8080/dispatch \
  -H "Authorization: Bearer $(grep WF_BEARER_TOKEN .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"preprint_doi": "10.1101/2024.05.03.24306688", "published_doi": "10.1007/978-3-031-66535-6_19"}'
# Expect: {"status": "accepted"} in <100ms
```

The reference golden envelope above is the T1 real-N>0 baseline; see
[tests/golden/](tests/golden/) for the captured outputs.

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
| `USE_STUB_STREAM` | env | unset in prod; `1` locally to replay `_stub_stream.json` |
| `DEMO_FALLBACK_EMAIL` | env | recipient when notifier's `recipient_email` is null (§3.3 v0 limitation — citing_paper_authors[].email is always null). Leave unset in production. |

Gmail OAuth credentials (`gmail-refresh-token`, `gmail-oauth-client-id`,
`gmail-oauth-client-secret`) are read at first send from Secret Manager —
not env vars. See `GMAIL_SECRETS` in [main.py](main.py).

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
#    The four runtime flags below are mandatory; see "Runtime sizing" below.
gcloud run deploy claimdrift-dispatcher \
  --image=us-central1-docker.pkg.dev/tensile-topic-496519-i1/cloud-run-source-deploy/claimdrift-dispatcher:latest \
  --region=us-central1 \
  --project=tensile-topic-496519-i1 \
  --allow-unauthenticated \
  --min-instances=1 --max-instances=20 --concurrency=1 --cpu-boost \
  --update-env-vars="GCP_PROJECT=tensile-topic-496519-i1,GCP_REGION=us-central1,SUPERVISOR_REASONING_ENGINE_ID=<id>,ELASTIC_ENDPOINT=<url>,DEMO_FALLBACK_EMAIL=<addr>" \
  --set-secrets="WF_BEARER_TOKEN=wf-bearer-token:latest,ELASTIC_API_KEY=elastic-api-key:latest"
```

### Runtime sizing: `--concurrency=1` + `--max-instances=20` + `--cpu-boost` + `--min-instances=1`

Each `POST /dispatch` returns 202 in <100ms but spawns a ~200s background
task (`asyncio.create_task(run_pipeline)`) that streams from Vertex AI Agent
Engine and writes back to ES. Cloud Run's defaults (concurrency=80, one
instance) collapse under this pattern: a single instance ends up trying to
serve a fresh handler **while** a previous handler's background task still
holds the event loop, so subsequent POSTs queue behind the in-flight pipeline
and the Elastic Workflow `http.request` connector's hard 60s timeout fires.
The Workflow execution then errors out and **`write_new_watermark` is
skipped**, which silently freezes the backlog cursor (dispatcher idempotency
masks the symptom — envelopes still flow for the small subset of
unprocessed pairs, but the visible drift_events count barely moves).

The four flags together produce the desired isolation:

| Flag | Why |
|---|---|
| `--concurrency=1` | One instance handles one POST + its background pipeline. No event-loop contention between concurrent dispatches. |
| `--max-instances=20` | A single 5-min workflow tick fans out up to 20 POSTs (`search_new_pairs.size = 20`); each lands on its own Cloud Run instance. 20 is also a hard ceiling so the backlog can't spiral. |
| `--cpu-boost` | Doubles CPU for 5s on container start. Brings cold-start of the vertexai SDK + Agent Engine reasoning engine resolve from ~40s down to ~10-20s, comfortably inside the workflow's 60s timeout. |
| `--min-instances=1` | Keeps one instance always warm so the first POST after an idle period is also fast (defense in depth on top of `--cpu-boost`). |

All four are live as of 2026-05-28. Costs are modest: one always-on
Cloud Run instance + up to 20 short-lived instances during workflow
fan-out (each lives ~3-5 minutes). No GPU.

The Artifact Registry path `us-central1-docker.pkg.dev/<project>/cloud-run-source-deploy/<service>`
is the default location `gcloud run deploy --source` writes to on first use; the
`cloud-run-source-deploy` repo is auto-created the first time you deploy. If you
already have an existing repo by another name, update both lines accordingly.

`--allow-unauthenticated` is intentional: the Elastic Workflow `http.request`
step calls this endpoint with only a bearer header, no GCP IAM identity. The
bearer check inside the handler is the only auth layer — defense-in-depth via
Cloud Run IAM (OIDC tokens from the Workflow side) is post-v0 polish.

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
