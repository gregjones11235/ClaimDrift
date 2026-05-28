# ClaimDrift BFF

This directory holds the Python BFF (`server.py`) that the Next.js frontend talks to. It serves
REST views over Elasticsearch and a Server-Sent Events channel for the live agent activity timeline
(translating dispatcher-written `agent_events` rows into the contracts.md §6.1 envelope shape).

## Run Against Elasticsearch (default)

`server.py` auto-loads `agents/.env` on startup, which provides `ELASTIC_ENDPOINT` and
`ELASTIC_API_KEY` (the same file used by the agents, dispatcher backfill, and `elastic/scripts/*`).
No `export` needed.

The BFF has no `pyproject.toml` of its own; it borrows the `agents/` uv environment, which
already includes `python-dotenv`. Run it via `--project agents`:

```bash
uv run --project agents python apps/bff/server.py
```

You should see `ClaimDrift BFF running at http://127.0.0.1:8787 (elastic data source)`. Open:

```text
http://127.0.0.1:8787/api/health
http://127.0.0.1:8787/api/drift-events
http://127.0.0.1:8787/api/drift-events/<event_id>
http://127.0.0.1:8787/api/drift-events/<event_id>/claims
http://127.0.0.1:8787/api/drift-events/<event_id>/affected-citations
http://127.0.0.1:8787/api/drift-events/<event_id>/notifications
http://127.0.0.1:8787/api/patterns
http://127.0.0.1:8787/api/events/stream?drift_event_id=<event_id>
```

Pick a real `<event_id>` from the `/api/drift-events` list — the indices already hold the
production records written by the dispatcher.

Port override: `BFF_PORT=8790 uv run --project agents python apps/bff/server.py`.

### Including demo records (optional)

Records seeded by `elastic/scripts/seed_demo_to_es.py` are tagged `record_source=demo_seed`
and excluded by default. To surface them in the API:

```bash
BFF_INCLUDE_DEMO=1 uv run --project agents python apps/bff/server.py
```

## Fallback: run without Elastic credentials (local JSON seed)

For local development on a machine that has no `agents/.env` (e.g. a fresh clone, or an
evaluator machine), the BFF falls back to reading `elastic/demo_seed/*.json` directly:

```bash
uv run --project agents python elastic/scripts/seed_demo_cases.py
uv run --project agents python apps/bff/server.py
```

`/api/health` will report `"data_source": "seed"`. In this mode `BFF_INCLUDE_DEMO` has no
effect — demo records are always returned.

`/api/events/stream` has three modes (`server.py` picks based on env + data source):

| Mode | Trigger | What gets streamed |
|---|---|---|
| **Live ES tail** | Elastic creds set | Tails the `agent_events` index for the requested `drift_event_id` / `dispatch_id`. Dispatcher writes envelopes in real time; BFF flushes them to the frontend as they appear. Supports `Last-Event-ID` for reconnect. |
| **Golden replay** | `SSE_REPLAY_GOLDEN=1` + `apps/dispatcher/tests/golden/stream_amblyopia_v2.jsonl` present | Replays the checked-in T1 ADK event stream through the production translator (`sse_adapter.translate_adk_event`). Lets evaluators see the production event flow without GCP credentials. |
| **Static fallback** | Seed mode, no replay flag | Hand-coded 9-event sequence. Kept only as a last-resort fallback. |

Tuning env vars for the live tail mode: `SSE_TAIL_POLL_S` (default 1.0), `SSE_TAIL_TIMEOUT_S` (300), `SSE_HEARTBEAT_S` (15).
