# ClaimDrift Ingestion Cloud Run Operations

Last updated: 2026-05-26

This document records the current B-side ingestion deployment state after the
Cloud Run and Elasticsearch backfill work.

## Current Status

- GCP project: `tensile-topic-496519-i1`
- Region: `us-central1`
- Elasticsearch endpoint: `https://claim-drift-e4bdf7.es.us-central1.gcp.elastic.cloud:443`
- Image: `us-central1-docker.pkg.dev/tensile-topic-496519-i1/claimdrift/ingestion-puller`
- Secret Manager key used by jobs: `elastic-api-key`
- Scheduler service account: `claimdrift-scheduler@tensile-topic-496519-i1.iam.gserviceaccount.com`

## Elasticsearch Backfill Result

Latest verified counts:

- Real `preprints` documents: `10,067`
- Real preprint/published pairs: `2,229+`
- Demo records are excluded with `must_not term record_source=demo_seed`.

This satisfies the B-side scale targets needed for C-side real e2e testing:

- Real preprints target: `>= 10,000`
- Real pair target: `>= 500`

## Cloud Run Jobs

Three ingestion jobs are deployed:

- `claimdrift-biorxiv-puller`
- `claimdrift-medrxiv-puller`
- `claimdrift-crossref-puller`

Current steady-state job args:

```text
claimdrift-biorxiv-puller:
  --source=biorxiv
  --since=2026-05-25
  --limit=300
  --include-published
  --apply

claimdrift-medrxiv-puller:
  --source=medrxiv
  --since=2026-05-25
  --limit=300
  --include-published
  --apply

claimdrift-crossref-puller:
  --source=crossref-batch
  --batch-source=all
  --limit=100
  --apply
```

These are incremental settings. Do not leave historical backfill settings such
as `--since=2023-01-01 --limit=4000` on the scheduled jobs.

## Cloud Scheduler

The three Scheduler jobs are enabled and use `America/New_York` time zone:

- `claimdrift-biorxiv-daily`
- `claimdrift-medrxiv-daily`
- `claimdrift-crossref-daily`

The names still say `daily`, but the cadence can be hourly per the contract.
The recommended hourly schedule is staggered to avoid simultaneous Cloud Run
executions:

```text
bioRxiv:  0 * * * *
medRxiv: 10 * * * *
crossref: 30 * * * *
```

## Verification Commands

List Scheduler jobs:

```bash
gcloud scheduler jobs list --location=us-central1
```

Inspect job args:

```bash
gcloud run jobs describe claimdrift-biorxiv-puller --region=us-central1 --format=yaml | grep -A15 "args:"
gcloud run jobs describe claimdrift-medrxiv-puller --region=us-central1 --format=yaml | grep -A15 "args:"
gcloud run jobs describe claimdrift-crossref-puller --region=us-central1 --format=yaml | grep -A15 "args:"
```

Count real preprints:

```bash
curl -s -X POST "$ELASTIC_ENDPOINT/preprints/_count" \
  -H "Authorization: ApiKey $ELASTIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":{"bool":{"must_not":[{"term":{"record_source":"demo_seed"}}]}}}'
```

Count real preprint/published pairs:

```bash
curl -s -X POST "$ELASTIC_ENDPOINT/preprints/_count" \
  -H "Authorization: ApiKey $ELASTIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "bool": {
        "filter": [{"exists": {"field": "published_doi"}}],
        "must_not": [
          {"term": {"record_source": "demo_seed"}},
          {"term": {"version": "published"}}
        ]
      }
    }
  }'
```

By-source aggregation:

```bash
curl -s -X POST "$ELASTIC_ENDPOINT/preprints/_search?size=0" \
  -H "Authorization: ApiKey $ELASTIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "bool": {
        "must_not": [{"term": {"record_source": "demo_seed"}}]
      }
    },
    "aggs": {
      "by_source": {
        "terms": {"field": "source", "size": 10}
      }
    }
  }'
```

## Notes

- `bioRxiv` and `medRxiv` pulls now use cursor-based pagination.
- Elasticsearch writes are split through bulk batches.
- CLI output defaults to summary-only logs. Use `--include-items` for local
  record inspection.
- `crossref-batch` is intentionally smaller because it performs one Crossref
  lookup per candidate DOI.
- The arXiv puller is still not implemented.
- A future production hardening step should add checkpoint-based incremental
  ingestion rather than relying on a static `--since` window.
