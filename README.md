# ClaimDrift

> When a preprint becomes a peer-reviewed paper, its claims can shift — sometimes subtly, sometimes substantially. ClaimDrift detects these drifts and notifies the downstream researchers whose work depends on the original.

Submission for the [Google Cloud Rapid Agent Hackathon](https://rapid-agent.devpost.com/), Elastic Track.

The authoritative cross-component specification lives in [docs/contracts.md](docs/contracts.md). This README is the entry point for evaluators / new contributors; contracts.md is the source of truth for every schema, interface, and design decision.

---

## What the system does

```
                bioRxiv / medRxiv / Crossref           OpenAlex
                          │                                │
                          ▼                                │
                   Ingestion pipeline                      │
              (4 Cloud Run Jobs + Cloud Scheduler)         │
                          │                                │
                          ▼                                │
              ┌──── Elasticsearch (6 indices) ◄────────────┤
              │           ▲       ▲                        │
              │           │       │                        │
              │   Elastic Agent Builder MCP server          │
              │   (4 tools: ES|QL + Workflow YAML)         │
              │           ▲                                │
              │           │ MCP                            │
              │           │                                │
              │   Vertex AI Agent Engine                   │
              │   ┌────────────────────────┐               │
              │   │  supervisor (ADK)      │               │
              │   │   ├─ claim_extractor   │               │
              │   │   ├─ drift_analyzer    │               │
              │   │   ├─ citation_finder ──┼───────────────┘
              │   │   ├─ notifier          │
              │   │   └─ memory_synthesizer│
              │   └────────────────────────┘
              │           ▲
              │           │ async_stream_query
              │           │
              │   Cloud Run dispatcher  ◄───┐
              │   (Gmail OAuth send)        │
              │                             │ HTTP POST
              └─────────────────────────────┤
                                            │
                              Elastic Scheduled Workflow
                              (cron 5min, watermark cursor)
```

Every 5 minutes, the scheduled Elastic Workflow finds newly-ingested `(preprint, published)` pairs in `preprints`, POSTs each to the Cloud Run dispatcher, which runs the §4.1 fan-out across the 5 ADK agents on Agent Engine and persists results back to Elasticsearch.

For the design rationale of this inverted topology (why supervisor lives on Agent Engine and Elastic Workflows is "only" the trigger), see [contracts.md §9.6.1](docs/contracts.md).

---

## Current deployment state (2026-05-29)

| Component | Status | Notes |
|---|---|---|
| 5 sub-agents on Vertex AI Agent Engine | ✓ live | `claim_extractor`, `drift_analyzer`, `citation_finder`, `notifier`, `memory_synthesizer` — see `agents/_DEPLOY_CHECKLIST.md` |
| supervisor on Agent Engine | ✓ live | reasoningEngine `7816826734824652800` |
| Elastic Agent Builder MCP tools | ✓ live | 4 tools wired: `search_drift_patterns` (ES\|QL) + `create_drift_pattern` / `update_drift_pattern` (Workflow YAML write side) + `openalex_citing_works` (Workflow YAML http chain) |
| Elasticsearch indices | ✓ live | 8 indices (6 business + `dispatch_state` watermark + `agent_events` SSE stream); ~10k real preprints + ~2.2k real (preprint, published) pairs |
| Ingestion pipeline | ✓ live | 3 Cloud Run Jobs (`bioRxiv`, `medRxiv`, `crossref`) + Cloud Scheduler hourly; see [docs/ingestion_cloud_run_ops.md](docs/ingestion_cloud_run_ops.md) |
| Cloud Run dispatcher | ✓ live | `https://claimdrift-dispatcher-3gz4czm2hq-uc.a.run.app/dispatch` — receives Workflow POSTs, drives supervisor, persists outputs, sends Gmail; idempotent on `(preprint_doi, published_doi)` |
| Elastic Scheduled Workflow | ✓ live | `dispatch_new_pairs`, every 5 min |
| BFF (`apps/bff/`) | ✓ live | `server.py` serves REST views over ES + the live `/api/events/stream` SSE channel; SSE adapter (Agent Engine streamQuery → §6.1 envelope) shipped 2026-05-28, see [docs/contracts.md §6.3](docs/contracts.md). Cloud Run deploy artifacts in `apps/bff/Dockerfile` + `cloudbuild.yaml` |
| Frontend (`frontend/`) | ✓ live | Next.js 16 + React 19, 6 views, wired to real data via the BFF; D-owned. Cloud Run deploy artifacts in `frontend/Dockerfile` + `cloudbuild.yaml` |
| arXiv puller | ✗ out of scope | Dropped 2026-05-26 — bioRxiv + medRxiv already cover ~10k preprints, OAI-PMH complexity unnecessary for the §3.5 memory-loop demo |

---

## Repository layout

| Directory | Owner | Status | Contents |
|---|---|---|---|
| [`agents/`](agents/) | C | ✓ deployed | 5 sub-agents + supervisor + `_DEPLOY_CHECKLIST.md` |
| [`apps/dispatcher/`](apps/dispatcher/) | C | ✓ deployed | Cloud Run service driving the §9.6.1 main flow; T1 golden artifacts in `tests/golden/` |
| [`apps/bff/`](apps/bff/) | C / B | ✓ live | Python BFF + SSE for frontend, serving real ES data |
| [`ingestion/`](ingestion/) | B | ✓ deployed | bioRxiv / medRxiv / Crossref pullers + Cloud Run + Cloud Scheduler |
| [`elastic/`](elastic/) | C / B | ✓ live | Mappings, demo seed, MCP tool YAMLs, scheduled workflow YAML, audit script |
| [`frontend/`](frontend/) | D | ✓ live | Next.js dashboard, 6 views over the BFF |
| [`contracts/`](contracts/) | C / D | partial | Shared TypeScript types for frontend ↔ BFF; see `claimdrift_types.ts` |
| [`docs/`](docs/) | All | ✓ current | `contracts.md` (authoritative spec + changelog) + ingestion ops runbooks |

---

## Reproduce from a clean clone

Prerequisites:

- **GCP**: a project with Vertex AI, Cloud Run, Cloud Build, Secret Manager, Cloud Scheduler APIs enabled
- **Elastic**: a serverless project with ELSER + Agent Builder enabled. Kibana URL (`*.kb.*`) and ES URL (`*.es.*`) are distinct — both are needed.
- **Gmail OAuth**: a project-scoped OAuth client (Desktop type) for the notifier service account
- **Tooling**: `uv` (Python), `gcloud` CLI logged in to the GCP project, `bash` / WSL on Windows

Setup, in dependency order — each step is verifiable independently:

1. **Elasticsearch indices**
   ```bash
   set -a; source agents/.env; set +a   # exports ELASTIC_ENDPOINT + ELASTIC_API_KEY
   uv run python elastic/scripts/create_indices.py --apply --skip-existing
   ```
   Verify with the schema-drift audit:
   ```bash
   uv run python elastic/scripts/audit_schema_drift.py    # should print "all 8 indices clean"
   ```

2. **Elastic Agent Builder MCP tools** — 1 ES|QL tool + 3 Workflow YAML tools. Each `elastic/agent_builder/tools/*.json` + `elastic/agent_builder/workflows/*.yaml` upserts via `upsert_tool.sh` / `upsert_workflow.sh`. See [contracts.md §9.3](docs/contracts.md) for the tool-by-tool migration table.

3. **5 sub-agents** to Vertex AI Agent Engine. Each agent in `agents/<name>/` has its own `requirements.txt` + `.env.deploy`; deploy follows [agents/_DEPLOY_CHECKLIST.md](agents/_DEPLOY_CHECKLIST.md). Save the resulting `reasoningEngine` numeric ids — they're hardcoded into `supervisor_agent/agent.py:SUB_AGENT_IDS`.

4. **Supervisor agent** to Agent Engine (same checklist; the supervisor is custom `BaseAgent` orchestration code, no LLM of its own).

5. **Cloud Run dispatcher**: two-step build + deploy from the **repo root** (build context must include `apps/bff/sse_adapter.py` — the §6.1 translator shared with the BFF; `gcloud run deploy --source` has no `--dockerfile` flag for subdir paths). The four runtime flags below are **mandatory** — see [apps/dispatcher/README.md](apps/dispatcher/README.md) "Runtime sizing" for why each one matters:
   ```bash
   gcloud builds submit . --config=apps/dispatcher/cloudbuild.yaml
   gcloud run deploy claimdrift-dispatcher \
     --image=us-central1-docker.pkg.dev/<your-project>/cloud-run-source-deploy/claimdrift-dispatcher:latest \
     --region=us-central1 --allow-unauthenticated \
     --min-instances=1 --max-instances=20 --concurrency=1 --cpu-boost \
     --update-env-vars="GCP_PROJECT=<your-project>,SUPERVISOR_REASONING_ENGINE_ID=<from step 4>,ELASTIC_ENDPOINT=...,DEMO_FALLBACK_EMAIL=..." \
     --set-secrets="WF_BEARER_TOKEN=wf-bearer-token:latest,ELASTIC_API_KEY=elastic-api-key:latest"
   ```
   See [apps/dispatcher/README.md](apps/dispatcher/README.md) for env-var details + Gmail OAuth one-time setup.

6. **Ingestion pullers** to Cloud Run Jobs + Cloud Scheduler. See [docs/ingestion_cloud_run_ops.md](docs/ingestion_cloud_run_ops.md) for image, args, and scheduler config.

7. **Elastic Scheduled Workflow** — generates the production YAML from the template (bearer-token interpolation is the only step), then upsert:
   ```bash
   sed "s|<WF_BEARER_TOKEN>|$WF_BEARER_TOKEN|" \
     elastic/agent_builder/workflows/dispatch_new_pairs.template.yaml \
     > elastic/agent_builder/workflows/dispatch_new_pairs.yaml
   elastic/agent_builder/scripts/upsert_workflow.sh \
     elastic/agent_builder/workflows/dispatch_new_pairs.yaml
   ```
   Enable in Kibana (or `PATCH /api/workflows/dispatch-new-pairs {"enabled":true}`).

8. **Initialize the watermark** (one-time):
   ```bash
   curl -X PUT "$ELASTIC_ENDPOINT/dispatch_state/_doc/main_flow" \
     -H "Authorization: ApiKey $ELASTIC_API_KEY" -H "Content-Type: application/json" \
     -d '{"flow_name":"main_flow","last_seen_ingested_at":"<now>","last_updated_at":"<now>"}'
   ```

9. **Frontend + BFF (the hosted demo)** — see "Deploy the hosted demo" below and [`frontend/README.md`](frontend/README.md).

Verify the end-to-end path against the T1 reference run:

```bash
# Manual dispatch of the canonical T1 pair (amblyopia LLM detection preprint)
curl -X POST "https://<your-dispatcher>/dispatch" \
  -H "Authorization: Bearer $WF_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"preprint_doi":"10.1101/2024.05.03.24306688","published_doi":"10.1007/978-3-031-66535-6_19"}'
# First run: 202 + ~200s background pipeline writes 1 drift_event + 4 affected_citations + 4 notification_log rows
# Second run: 200 {"status":"already_processed","drift_event_id":...} — dispatcher idempotency
```

Reference outputs are checked in at [`apps/dispatcher/tests/golden/`](apps/dispatcher/tests/golden/).

---

## Deploy the hosted demo (frontend + BFF)

The agents, dispatcher, and ingestion pipeline already run on Google Cloud (above). The Devpost **"URL to the hosted Project for judging and testing"** needs the *user-facing* layer online too: the Next.js dashboard + the BFF it reads from. Both deploy to **Cloud Run** so the entire hosted demo stays on Google Cloud (no third-party PaaS).

Two services, deployed BFF-first (the frontend bakes in the BFF URL at build time):

1. **BFF** → Cloud Run. Build context is the repo root (it bundles `ingestion/common` + `apps/bff/sse_adapter.py`), so it goes through Cloud Build like the dispatcher:
   ```bash
   gcloud builds submit . --config=apps/bff/cloudbuild.yaml
   gcloud run deploy claimdrift-bff \
     --image=us-central1-docker.pkg.dev/<your-project>/cloud-run-source-deploy/claimdrift-bff:latest \
     --region=us-central1 --allow-unauthenticated \
     --update-env-vars="ELASTIC_ENDPOINT=https://<your-es-endpoint>" \
     --set-secrets="ELASTIC_API_KEY=elastic-api-key:latest"
   # Note the printed service URL — call it <BFF_URL> below.
   ```
   `server.py` honors Cloud Run's `$PORT` and binds `0.0.0.0` automatically; CORS is already `*` so the browser can call it cross-origin.

2. **Frontend** → Cloud Run. `NEXT_PUBLIC_BFF_URL` is inlined into the client bundle, so it must be passed at **build** time:
   ```bash
   cd frontend
   gcloud builds submit . --config=cloudbuild.yaml --substitutions=_BFF_URL=<BFF_URL>
   gcloud run deploy claimdrift-frontend \
     --image=us-central1-docker.pkg.dev/<your-project>/cloud-run-source-deploy/claimdrift-frontend:latest \
     --region=us-central1 --allow-unauthenticated
   ```

The **frontend** Cloud Run URL is what goes in the Devpost "hosted Project" field. (The separate "open source code repository" field is this GitHub repo.)

> Local alternative for development only: run `uv run --project agents python apps/bff/server.py` + `npm run dev` in two terminals (see [`frontend/README.md`](frontend/README.md)). That is **not** a valid submission URL — judges can't reach `localhost`; deploy to Cloud Run for the hosted link.

---

## Tech stack

- **Agents**: Google Agent Development Kit (ADK) + Gemini 2.5 (flash + pro), deployed on Vertex AI Agent Engine
- **Search + memory**: Elasticsearch Serverless + ELSER semantic_text + Elastic Agent Builder MCP server
- **Orchestration**: ADK supervisor on Agent Engine + Elastic Scheduled Workflow as trigger source (inverted topology, §9.6.1)
- **Trigger / persistence**: Cloud Run dispatcher (FastAPI) + Gmail API for email send
- **Frontend**: Next.js 16 + React 19 + Tailwind + shadcn/ui, deployed on Cloud Run (D)
- **Data sources**: bioRxiv, medRxiv, Crossref, OpenAlex

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
