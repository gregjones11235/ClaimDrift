# claimdrift-dispatcher

Entry of the automatic data pipeline into layer ① (新系统云端部署.md §4.6):

```
pullers (Cloud Run Jobs claimdrift-{biorxiv,medrxiv,crossref}-puller, hourly Cloud Scheduler)
  -> ES preprints (rows with published_doi, is_final_preprint=true)
  -> Elastic Workflow dispatch-new-pairs (every 5 min; watermark dispatch_state/_doc/main_flow, oldest first)
  -> POST /dispatch (bearer token = secret wf-bearer-token)
       budget check -> idempotency (drift_event for the pair?) -> pre-check (v1 JATS, open published full text) -> Pub/Sub
  -> topic claimdrift-dispatch -> push subscription claimdrift-dispatch-push (OIDC as claimdrift-run, ack 600 s)
  -> POST /run -> claimdrift.pipeline.analyze_dois
       v1 from its own JATS + published JATS (docstore on the /data bucket) -> supervisor on Vertex AI Agent Engine
       (claim_extractor x2 -> drift_analyzer -> quote verification via MCP) -> drift_events, review triggers
       -> citation runs queued (processed by the claimdrift-citation-dispatch job, which notifies the test inbox only)
```

Nothing is e-mailed from the dispatcher.

## Endpoints

| | |
|---|---|
| `GET /health` | status and the dispatch budget left |
| `POST /dispatch` | `{"preprint_doi", "published_doi"}`, header `Authorization: Bearer <wf-bearer-token>`; answers in a few seconds (the workflow's http step has a fixed 60 s limit on Serverless). `?force=true` skips the idempotency check |
| `POST /run` | Pub/Sub push target; Google OIDC token signed as `PUBSUB_PUSH_SA_EMAIL` with audience `PUBSUB_PUSH_AUDIENCE`; holds the request for the whole analysis |

`/dispatch` answers: `enqueued`, `already_processed`, `not_analysable` (with the pre-check reason; costs no budget), or
`paused_budget_exhausted` (200, so no failed workflow run). Deferred pairs are not lost: `pipeline.sh batch` rewinds the
watermark to just before the oldest pair without a drift event (`claimdrift/scripts/pipeline_watermark.py`).

## Budget: one batch, then pause

`dispatch_state/_doc/dispatch_budget` = `{budget_max, budget_used, analyzed_pairs}`. Each analysable pair published to
Pub/Sub uses one unit. When the budget is used up the dispatcher pauses the whole pipeline itself: the puller schedules
(`PAUSE_SCHEDULER_JOBS`) and the workflow (disabled through Kibana, `KIBANA_URL`), so while paused nothing runs and no
failed run is reported. Operate it with:

```bash
bash deploy/pipeline.sh deploy      # build + deploy everything, paused (budget 0)
bash deploy/pipeline.sh batch 5     # pull and analyse one batch of 5 pairs, then stop by itself
bash deploy/pipeline.sh pause       # stop now
bash deploy/pipeline.sh status
```

## Configuration (Cloud Run env, set by deploy/pipeline.sh)

`GCP_PROJECT`, `GCP_REGION`, `PUBSUB_TOPIC` (default `claimdrift-dispatch`), `PUBSUB_PUSH_SA_EMAIL`,
`PUBSUB_PUSH_AUDIENCE` (`<service URL>/run`), `PAUSE_SCHEDULER_JOBS`, `KIBANA_URL`, `WORKFLOW_ID`; secrets `WF_BEARER_TOKEN`,
`CLAIMDRIFT_ES_API_KEY`; claimdrift settings `CLAIMDRIFT_ES`, `CLAIMDRIFT_MCP_URL`, `CLAIMDRIFT_ENGINES`,
`CLAIMDRIFT_DATA=/data` (bucket mounted read-write: the fetched JATS go into the docstore the MCP service reads).
Runtime: `--concurrency=4 --max-instances=3 --min-instances=0 --timeout=600`: /run mostly waits on Agent Engine, and with one request per instance three long analyses left no instance for /dispatch (Cloud Run answered "no available instance" and the workflow tick failed).

## Local run

```bash
GCP_PROJECT=gen-lang-client-0220563082 WF_BEARER_TOKEN=dev PUBSUB_PUSH_SA_EMAIL=x \
  uv run uvicorn apps.dispatcher.main:app --port 8080
```

## Other files

`scripts/backfill_dispatch_pairs.py` posts every eligible pair in `preprints` to `/dispatch` directly, bypassing the
workflow (`DISPATCHER_URL` or `--dispatcher-url`, bearer token in `WF_BEARER_TOKEN`); the dispatch budget still applies.
`tests/test_versions_local.py` covers `versions.py`. The pre-2026-10 tools (supervisor stream replay, Gmail send tests,
notification resend) were removed; they are in the git history.
