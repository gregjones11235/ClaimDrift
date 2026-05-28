# ClaimDrift BFF

This directory holds the Python BFF (`server.py`) that the Next.js frontend talks to. It serves
REST views over Elasticsearch and a Server-Sent Events channel for the live agent activity timeline
(translating dispatcher-written `agent_events` rows into the contracts.md §6.1 envelope shape).

## Run With Demo Seed Data (local JSON)

```bash
python3 elastic/scripts/seed_demo_cases.py
python3 apps/bff/server.py
```

If `8787` is already in use:

```bash
BFF_PORT=8790 python3 apps/bff/server.py
```

Open:

```text
http://127.0.0.1:8787/api/health
http://127.0.0.1:8787/api/drift-events
http://127.0.0.1:8787/api/drift-events/demo-drift-001
http://127.0.0.1:8787/api/drift-events/demo-drift-001/claims
http://127.0.0.1:8787/api/drift-events/demo-drift-001/affected-citations
http://127.0.0.1:8787/api/drift-events/demo-drift-001/notifications
http://127.0.0.1:8787/api/patterns
http://127.0.0.1:8787/api/events/stream
```

> **Note:** In seed mode, `BFF_INCLUDE_DEMO` has no effect. All records come directly from
> `elastic/demo_seed/*.json` and are always returned. The `/api/health` response will show
> `"include_demo_records": false` if the env var is unset, but demo data is never filtered.
> Use Elasticsearch mode (below) for real isolation.

## Run Against Elasticsearch

If Elastic credentials are present, the same server reads from real Elasticsearch indices instead of `elastic/demo_seed`.

```bash
export ELASTIC_ENDPOINT="https://your-cluster.example.com"
export ELASTIC_API_KEY="..."

python3 elastic/scripts/create_indices.py --apply --skip-existing
python3 elastic/scripts/seed_demo_to_es.py --apply
python3 apps/bff/server.py
```

Demo seed records written to Elasticsearch are tagged with `record_source=demo_seed`. The BFF excludes them by default in Elasticsearch mode. To include demo records:

```bash
BFF_INCLUDE_DEMO=1 python3 apps/bff/server.py
```

Verified behaviour (Elastic Cloud, 2026-05-22):

| `BFF_INCLUDE_DEMO` | `data_source` | `drift-events` count |
|--------------------|---------------|----------------------|
| `1`                | elastic       | 2 (demo records visible) |
| unset              | elastic       | 0 (demo records filtered) |

`/api/events/stream` has three modes (`server.py` picks based on env + data source):

| Mode | Trigger | What gets streamed |
|---|---|---|
| **Live ES tail** | Elastic creds set | Tails the `agent_events` index for the requested `drift_event_id` / `dispatch_id`. Dispatcher writes envelopes in real time; BFF flushes them to the frontend as they appear. Supports `Last-Event-ID` for reconnect. |
| **Golden replay** | `SSE_REPLAY_GOLDEN=1` + `apps/dispatcher/tests/golden/stream_amblyopia_v2.jsonl` present | Replays the checked-in T1 ADK event stream through the production translator (`sse_adapter.translate_adk_event`). Lets evaluators see the production event flow without GCP credentials. |
| **Static mock** | Seed mode, no replay flag | Hand-coded 9-event sequence. Kept only as a last-resort fallback. |

Tuning env vars for the live tail mode: `SSE_TAIL_POLL_S` (default 1.0), `SSE_TAIL_TIMEOUT_S` (300), `SSE_HEARTBEAT_S` (15).
