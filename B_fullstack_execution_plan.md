# ClaimDrift — B Full-stack Execution Plan

Owner: B / Jeremy  
Scope: ingestion pipeline, Elasticsearch mappings + ELSER semantic retrieval, BFF/SSE, notifier dispatch.

## 0. Core Goal

Turn the inter-component contract into runnable backend and data infrastructure:

1. Data can be ingested from arXiv, bioRxiv, medRxiv, Crossref, and OpenAlex.
2. Data can be read consistently by Agent Builder and the frontend through six Elasticsearch indexes, mappings, and ELSER-backed semantic fields.
3. Agent execution can be streamed to the frontend through the BFF and Server-Sent Events.
4. Notifier outputs can be drafted, persisted, and optionally dispatched through a notification adapter.

## 1. Recommended Directory Layout

```text
claimdrift-backend/
  apps/
    bff/
      src/
        routes/
        sse/
        elastic/
        types/
      package.json
    notifier/
      src/
        dispatch/
        templates/
        index.ts
  ingestion/
    pullers/
      arxiv_puller.py
      biorxiv_puller.py
      medrxiv_puller.py
      crossref_puller.py
      openalex_client.py
    common/
      doi.py
      elastic.py
      logging.py
      rate_limit.py
  elastic/
    mappings/
      preprints.json
      claims.json
      drift_events.json
      affected_citations.json
      drift_patterns.json
      notification_log.json
    ingestion/
      elser_ingest_pipeline.json
    scripts/
      create_indices.py
      reset_demo_indices.py
  contracts/
    claimdrift_contracts.md
    generated_types.ts
  infra/
    cloud_run_jobs/
    scheduler/
  tests/
```

## 2. Day 1-2: Lock the Data Contract

Deliverables:

- Keep the source contract in `docs/contracts.md`.
- Generate or maintain shared TypeScript types for backend and frontend.
- Define the first complete mapping version for all six indexes.
- Use `semantic_text` directly for semantic fields on Elastic Serverless.

Highest-priority semantic fields:

- `preprints.abstract`
- `preprints.conclusion`
- `claims.text`
- `drift_patterns.pattern_description`

These fields must support semantic retrieval. Otherwise, Agent Builder retrieval and the memory loop become placeholders instead of working system behavior.

## 3. Day 2-3: Elasticsearch and ELSER

Required outputs:

- `elastic/mappings/*.json`
- `elastic/pipelines/elser_ingest_pipeline.json` as a deprecated placeholder only
- `elastic/scripts/create_indices.py`
- A one-command demo environment initialization path

Implementation notes:

- Normalize DOI values to lowercase and remove the `https://doi.org/` prefix.
- Generate index `_id` values according to the contract so repeated ingestion is idempotent.
- Demo-stage reset/recreate scripts are acceptable, but they must be clearly marked as demo-only.
- Avoid overcomplicating mappings early. First make field types and query paths correct.

## 4. Day 3-5: Ingestion Pullers

Suggested implementation order:

1. `crossref_puller`: bridge preprint DOI to published DOI first.
2. `biorxiv_puller` / `medrxiv_puller`: these APIs are direct and useful for demo data quickly.
3. `arxiv_puller`: handle OAI-PMH and the 3-second polite rate limit.
4. `openalex_client`: Citation Finder utility that returns citing works from OpenAlex; it does not directly write ES.

Common puller interface:

```python
def run_pull(source: str, since: str | None = None, limit: int | None = None) -> dict:
    """
    Returns:
      {
        "source": "...",
        "fetched": 0,
        "upserted": 0,
        "skipped": 0,
        "errors": []
      }
    """
```

Required behavior:

- Polite `User-Agent`
- Retry with backoff
- Structured logs
- Bulk upsert
- DOI normalization
- Dry-run mode

## 5. Day 5-6: BFF and SSE

Minimum BFF API:

```text
GET /api/drift-events
GET /api/drift-events/:id
GET /api/drift-events/:id/claims
GET /api/drift-events/:id/affected-citations
GET /api/drift-events/:id/notifications
GET /api/patterns
GET /api/events/stream
```

SSE events must follow the contract:

```json
{
  "event_type": "agent.started",
  "agent_id": "drift_analyzer",
  "drift_event_id": "uuid-or-null",
  "timestamp": "2026-05-20T12:34:56Z",
  "payload": {}
}
```

Implement a mock event source first so the frontend can build the timeline before real Agent Builder events are available.

The BFF should support:

- `GET /api/events/stream?drift_event_id=...`
- Heartbeat events with `event_type = "heartbeat"`
- Reconnect support through SSE `id:` and frontend `Last-Event-ID`

## 6. Day 6-7: Notifier Dispatch

The Notifier agent owns the generated `subject` and `body`. B owns the dispatch boundary:

- Receive Notifier output.
- Write `notification_log`.
- Send only to team-owned inboxes in demo mode.
- Record `sent`, `bounced`, `failed`, or `skipped`.

Use a provider adapter structure:

```text
dispatch/
  base.ts
  smtp.ts
  resend.ts
  sendgrid.ts
  dry_run.ts
```

Default to `dry_run`; enable real dispatch through an environment variable:

```text
NOTIFIER_MODE=dry_run | smtp | resend | sendgrid
```

## 7. Interface Checkpoints with C and D

Coordinate with C on:

- ES tool names used by Agent Builder.
- The query shape for `drift_patterns` hybrid search.
- How Agent Builder emitted events enter the BFF.
- Which workflow step owns each ES write.

Coordinate with D on:

- Which endpoints are needed by each of the six frontend views.
- Whether `agent.pattern_retrieved` should include full `pattern_description` values.
- Whether the claim diff view needs highlight spans beyond `preprint_text` and `published_text`.
- Whether the citation graph needs backend-shaped nodes and edges or can derive them from affected citations.

## 8. Risk Register

| Risk | B-side mitigation |
|------|-------------------|
| ELSER inference endpoint is not ready | Keep a plain `text` query fallback so the demo is not blocked |
| Crossref cannot resolve the published DOI | Seed published DOI values manually for demo cases |
| OpenAlex citation data is incomplete | Expose `processed` and `total_found` clearly to the frontend |
| Agent Builder events are not connected yet | Use mock SSE to unblock D |
| Email sending permissions or domains are not ready | Default to dry-run and only write `notification_log` |
| Mappings change late | Keep reset/recreate scripts for small demo datasets |

## 9. Minimum Demo Definition of Done

B-side MVP is complete when:

- The repo can seed two demo drift cases.
- `preprints`, `claims`, `drift_events`, `affected_citations`, `drift_patterns`, and `notification_log` can be created and queried.
- Each step in the ClaimDrift main flow has a corresponding ES record.
- The frontend can fetch drift event details, claim diffs, affected citations, patterns, and notification drafts through the BFF.
- The frontend can receive at least `agent.started`, `agent.pattern_retrieved`, `agent.completed`, and `agent.failed` over SSE.
- Notifier can dry-run by writing `notification_log`, and can send test email when a real provider is enabled.
