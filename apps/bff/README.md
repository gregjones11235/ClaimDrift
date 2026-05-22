# ClaimDrift BFF

This directory currently provides a lightweight Python BFF for frontend development.

## Run With Demo Seed Data (local JSON)

```bash
python3 elastic/scripts/seed_demo_cases.py
python3 apps/bff/mock_server.py
```

If `8787` is already in use:

```bash
BFF_PORT=8790 python3 apps/bff/mock_server.py
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
python3 apps/bff/mock_server.py
```

Demo seed records written to Elasticsearch are tagged with `record_source=demo_seed`. The BFF excludes them by default in Elasticsearch mode. To include demo records:

```bash
BFF_INCLUDE_DEMO=1 python3 apps/bff/mock_server.py
```

Verified behaviour (Elastic Cloud, 2026-05-22):

| `BFF_INCLUDE_DEMO` | `data_source` | `drift-events` count |
|--------------------|---------------|----------------------|
| `1`                | elastic       | 2 (demo records visible) |
| unset              | elastic       | 0 (demo records filtered) |

`/api/events/stream` is still a mock SSE stream until Agent Builder events are wired in.
