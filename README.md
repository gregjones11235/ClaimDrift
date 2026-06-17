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
              (3 Cloud Run Jobs + Cloud Scheduler)         │
                          │                                │
                          ▼                                │
              ┌──── Elasticsearch (8 indices) ◄────────────┤
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
              │   Cloud Run dispatcher /run
              │   (runs pipeline + Gmail OAuth send)
              │           ▲
              │           │ push (Google-signed OIDC)
              │   Pub/Sub topic `claimdrift-dispatch`
              │           ▲
              │           │ publish, then 202 in ~100ms
              │   Cloud Run dispatcher /dispatch
              │   (bearer auth + idempotency check)
              │           ▲
              │           │ HTTP POST
              └───────────┤
                          │
            Elastic Scheduled Workflow
            (cron 5min, watermark cursor)
```

Every 5 minutes, the scheduled Elastic Workflow finds newly-ingested `(preprint, published)` pairs in `preprints` and POSTs each to the dispatcher's `/dispatch` endpoint. `/dispatch` does bearer-auth + idempotency check, then publishes to the `claimdrift-dispatch` Pub/Sub topic and returns 202 in ~100ms (Elastic Workflows' `http` connector has a platform-fixed 60s timeout). A Pub/Sub push subscription delivers the message to the dispatcher's `/run` endpoint, which runs the §4.1 fan-out across the 5 ADK agents on Agent Engine (~200s) and persists results back to Elasticsearch. See [contracts.md §9.6.1 changelog 2026-05-28](docs/contracts.md) for why the pipeline is decoupled through Pub/Sub.

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
- **Tooling**: `uv` (Python), `gcloud` CLI logged in to the GCP project, `bash` / WSL on Windows, plus `jq` + `python3` on PATH (the Agent Builder upsert scripts in step 3 need them)

Setup, in dependency order — each step is verifiable independently.

0. **Environment + Python deps (do this first).** All `uv run …` commands below resolve against the single Python project at `agents/` (there is no repo-root `pyproject.toml`); they are run **from the repo root** so the top-level `ingestion` package is importable (the `elastic/scripts/*.py` tools `from ingestion.common.elastic import …`). The scripts themselves use only the standard library (`urllib`), so the sync is fast:
   ```bash
   cp agents/.env.example agents/.env   # then fill in every value — see the comments in the file
   uv sync --project agents             # creates agents/.venv with the ADK + deploy deps
   ```
   `agents/.env` is the single source the Python tools and the two Agent Builder shell scripts both read. Fill in **all** of: `GOOGLE_CLOUD_PROJECT`, `ELASTIC_ENDPOINT` (the `.es.` data-plane host), `ELASTIC_API_KEY`, and `KIBANA_URL` (the `.kb.` host — distinct from `.es.`, required by step 3's upsert scripts). It is gitignored, so a clean clone has only `.env.example`.

   Every subsequent step assumes the endpoint/key are exported into the shell:
   ```bash
   set -a; source agents/.env; set +a   # exports ELASTIC_ENDPOINT + ELASTIC_API_KEY + KIBANA_URL
   ```

1. **Elasticsearch indices**
   ```bash
   uv run python elastic/scripts/create_indices.py --apply --skip-existing
   ```
   Verify with the schema-drift audit:
   ```bash
   uv run python elastic/scripts/audit_schema_drift.py    # should print "all 8 indices clean"
   ```
   Then create the `drift_patterns_read` alias — the `search_drift_patterns` tool and the drift_analyzer memory read both query `FROM drift_patterns_read` (not the concrete index), so this must exist before step 2 or retrieval fails with `index_not_found`:
   ```bash
   uv run python elastic/scripts/manage_pattern_alias.py init --apply   # drift_patterns_read -> drift_patterns
   ```

   **1b. Populate `preprints` with data.** The indices are now empty. The scheduled workflow (step 7) fires on *newly-ingested* `(preprint, published)` pairs, and the step-9 smoke test reverse-looks-up its preprint DOI in `preprints` — both fail against an empty index. Pick whichever path fits your goal:

   **Option A — real backfill (faithful to the live system, takes minutes).** This is how the live data was produced (contracts §5.2 + [docs/ingestion_cloud_run_ops.md](docs/ingestion_cloud_run_ops.md)): a one-shot historical pull of bioRxiv + medRxiv with `--include-published`, then a Crossref-batch pass that backfills `published_doi` on rows with a real published match. It's the wide `--since/--limit` "historical backfill" window the ops runbook describes (the deployed Cloud Run Jobs in step 6 run a *narrow incremental* window instead). Use this if you want the real ~10k-preprint scale, or to exercise the workflow on genuinely new pairs.
   ```bash
   # Wide window, large limit. Drop --limit / widen --since to approach live scale.
   uv run python -m ingestion.run_pull --source biorxiv \
     --since 2023-01-01 --limit 4000 --include-published --bulk-batch-size 250 --apply
   uv run python -m ingestion.run_pull --source medrxiv \
     --since 2023-01-01 --limit 4000 --include-published --bulk-batch-size 250 --apply
   uv run python -m ingestion.run_pull --source crossref-batch \
     --batch-source all --limit 2500 --apply
   ```
   Verify the scale (demo rows excluded via `must_not record_source=demo_seed`) with the count queries in [docs/ingestion_cloud_run_ops.md](docs/ingestion_cloud_run_ops.md#L99) — targets are `>= 10,000` real preprints and `>= 500` real pairs.

   **Option B — demo seed (lightweight, takes seconds).** If you just want the end-to-end path running without waiting on external APIs, seed the curated demo cases instead. They write a handful of hand-built `(preprint, published)` pairs (each with a deliberate, inspectable drift) tagged `record_source="demo_seed"`:
   ```bash
   uv run python elastic/scripts/seed_demo_to_es.py --apply
   ```
   This is enough to drive the supervisor pipeline and the dashboard. Two caveats: the seed pairs already exist (they won't look "newly-ingested" to the step-7 workflow watermark — dispatch them manually as in step 9), and real-data views filter `demo_seed` out, so the BFF's real-data screens will look empty until you also run Option A. If you take Option B, use a **seed** pair for the step-9 smoke test, e.g. `{"preprint_doi":"10.1101/2024.01.15.123456","published_doi":"10.1016/j.cell.2024.05.001"}` (the hydroxychloroquine effect-size-reduction case), not the real-data T1 pair.

2. **Elastic Agent Builder MCP tools** — 4 tools in `elastic/agent_builder/tools/*.json`: `search_drift_patterns` is a self-contained ES|QL tool; the other three (`create_drift_pattern`, `update_drift_pattern`, `openalex_citing_works`) each delegate to a same-named Workflow YAML in `elastic/agent_builder/workflows/*.yaml` that must be upserted too. Both upsert scripts read `KIBANA_URL` + `ELASTIC_API_KEY` from `agents/.env` (the `.kb.` host, not `.es.`), and need `jq` + `python3` on PATH:
   ```bash
   for w in create_drift_pattern update_drift_pattern openalex_citing_works; do
     elastic/agent_builder/scripts/upsert_workflow.sh elastic/agent_builder/workflows/$w.yaml
   done
   for t in elastic/agent_builder/tools/*.json; do
     elastic/agent_builder/scripts/upsert_tool.sh "$t"
   done
   ```
   See [contracts.md §9.3](docs/contracts.md) for the tool-by-tool migration table.

3. **5 sub-agents** to Vertex AI Agent Engine. Each agent in `agents/<name>/` has its own `requirements.txt` + `.env.deploy`; deploy follows [agents/_DEPLOY_CHECKLIST.md](agents/_DEPLOY_CHECKLIST.md). Save the resulting `reasoningEngine` numeric ids — they're hardcoded into `supervisor_agent/agent.py:SUB_AGENT_IDS`.

4. **Supervisor agent** to Agent Engine (same checklist; the supervisor is custom `BaseAgent` orchestration code, no LLM of its own).

5. **Cloud Run dispatcher** — two endpoints (`/dispatch` enqueues to Pub/Sub; `/run` is the push-subscription target that runs the ~200s pipeline). The pipeline is decoupled through Pub/Sub because Elastic Workflows' `http` connector has a platform-fixed 60s timeout on Serverless (see [contracts.md changelog 2026-05-28](docs/contracts.md)). Three sub-steps:

   **5a. Build + deploy the service** from the **repo root** (build context must include `apps/bff/sse_adapter.py` — the §6.1 translator shared with the BFF; `gcloud run deploy --source` has no `--dockerfile` flag for subdir paths). The runtime flags below are **mandatory** — see [apps/dispatcher/README.md](apps/dispatcher/README.md) "Runtime sizing" for why each one matters:
   ```bash
   gcloud builds submit . --config=apps/dispatcher/cloudbuild.yaml
   gcloud run deploy claimdrift-dispatcher \
     --image=us-central1-docker.pkg.dev/<your-project>/cloud-run-source-deploy/claimdrift-dispatcher:latest \
     --region=us-central1 --allow-unauthenticated --timeout=600 \
     --min-instances=1 --max-instances=20 --concurrency=1 --cpu-boost \
     --update-env-vars="GCP_PROJECT=<your-project>,GCP_REGION=us-central1,SUPERVISOR_REASONING_ENGINE_ID=<from step 4>,ELASTIC_ENDPOINT=...,DEMO_FALLBACK_EMAIL=...,PUBSUB_PUSH_AUDIENCE=<dispatcher-url>/run" \
     --set-secrets="WF_BEARER_TOKEN=wf-bearer-token:latest,ELASTIC_API_KEY=elastic-api-key:latest"
   ```
   Note the printed service URL — call it `<DISPATCHER_URL>` below. (`PUBSUB_PUSH_AUDIENCE` needs the final URL, so on first deploy leave it unset, then re-run `gcloud run services update --update-env-vars=PUBSUB_PUSH_AUDIENCE=<DISPATCHER_URL>/run` once you have it.)

   **5b. Pub/Sub topic + push subscription.** `/dispatch` publishes here; the subscription pushes to `/run` with a Google-signed OIDC token (`ack-deadline=600s` covers the pipeline runtime). The push SA below is the project Compute SA — the default `/run` checks for (`PUBSUB_PUSH_SA_EMAIL`); override both sides if you use a dedicated SA. `PROJECT_NUMBER` comes from `gcloud projects describe <your-project> --format='value(projectNumber)'`:
   ```bash
   gcloud pubsub topics create claimdrift-dispatch

   PUSH_SA="<PROJECT_NUMBER>-compute@developer.gserviceaccount.com"
   # The push SA needs run.invoker to POST /run.
   gcloud run services add-iam-policy-binding claimdrift-dispatcher \
     --region=us-central1 --member="serviceAccount:$PUSH_SA" --role="roles/run.invoker"
   # The Pub/Sub-managed SA needs serviceAccountTokenCreator to mint the OIDC token
   # (not implicit post-2021).
   gcloud projects add-iam-policy-binding <your-project> \
     --member="serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-pubsub.iam.gserviceaccount.com" \
     --role="roles/iam.serviceAccountTokenCreator"

   gcloud pubsub subscriptions create claimdrift-dispatch-push \
     --topic=claimdrift-dispatch \
     --push-endpoint="<DISPATCHER_URL>/run" \
     --push-auth-service-account="$PUSH_SA" \
     --push-auth-token-audience="<DISPATCHER_URL>/run" \
     --ack-deadline=600
   ```
   `--push-auth-token-audience` MUST be set explicitly and match `PUBSUB_PUSH_AUDIENCE` from 5a, or Pub/Sub normalizes the scheme to `http://` and `/run`'s OIDC `aud` check fails.

   See [apps/dispatcher/README.md](apps/dispatcher/README.md) for the full env-var table + Gmail OAuth one-time setup.

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

Verify the end-to-end path against the T1 reference run. The dispatcher reverse-looks-up the preprint DOI in `preprints`, so this canonical pair must be in the index — the wide backfill in step 1b usually covers it, but if not, pull it directly first (the targeted "Real Pair For Dispatcher" recipe in [ingestion/README.md](ingestion/README.md)):

```bash
# (only if the T1 preprint isn't already in `preprints`)
uv run python -m ingestion.run_pull --source biorxiv \
  --since 2024-05-01 --limit 50 --include-published --apply
# --preprint-source biorxiv: this DOI is a bioRxiv preprint; the flag defaults to
# medrxiv, so it must be set explicitly or the published row gets a wrong source label.
uv run python -m ingestion.run_pull --source crossref \
  --doi 10.1101/2024.05.03.24306688 --preprint-source biorxiv --apply

# Manual dispatch of the canonical T1 pair (amblyopia LLM detection preprint)
curl -X POST "<DISPATCHER_URL>/dispatch" \
  -H "Authorization: Bearer $WF_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"preprint_doi":"10.1101/2024.05.03.24306688","published_doi":"10.1007/978-3-031-66535-6_19"}'
# First run:  {"status":"enqueued","message_id":...} in ~100ms — Pub/Sub then pushes to
#             /run, whose ~200s pipeline writes 1 drift_event + 4 affected_citations + 4
#             notification_log rows (watch progress in the Cloud Run logs for /run).
# Second run: {"status":"already_processed","drift_event_id":...} — dispatcher idempotency
#             (add ?force=true to re-run a pair anyway).
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
