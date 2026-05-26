# ClaimDrift — Ingestion

This directory contains B-owned data ingestion code for ClaimDrift.

For the current Cloud Run / Cloud Scheduler deployment state and operational
commands, see:

- [Ingestion Cloud Run Operations](../docs/ingestion_cloud_run_ops.md)
- [Ingestion Cloud Run 运维记录](../docs/ingestion_cloud_run_ops_CN.md)

It currently supports:

- bioRxiv pulls
- medRxiv pulls
- Crossref DOI lookup
- OpenAlex citing-work lookup for Citation Finder
- Contract-shaped dry-run output
- Optional Elasticsearch bulk upsert for `preprints`

## Dry Run

```bash
python3 -m ingestion.run_pull --source medrxiv --since 2024-05-01 --limit 3 --dry-run
python3 -m ingestion.run_pull --source biorxiv --since 2024-05-01 --limit 3 --dry-run
python3 -m ingestion.run_pull --source crossref --doi 10.1101/2024.01.15.123456 --dry-run
python3 -m ingestion.run_pull --source crossref-batch --batch-source all --limit 10 --dry-run
python3 -m ingestion.run_pull --source openalex --doi 10.1101/2024.01.21.24301585 --limit 5 --dry-run
```

By default, command output prints operational summary fields only. Add
`--include-items` when you need the normalized records in stdout for local
inspection.

## Write To Elasticsearch

```bash
export ELASTIC_ENDPOINT="https://your-cluster.example.com"
export ELASTIC_API_KEY="..."

python3 elastic/scripts/create_indices.py --apply
python3 -m ingestion.run_pull --source medrxiv --since 2024-05-01 --limit 3 --apply
```

`--apply` writes bioRxiv and medRxiv records to the `preprints` index. Add
`--include-published` to also create `version=published` rows for source records
whose API payload contains a real published DOI. Crossref `--apply` performs a
fallback dispatcher pairing step: it looks up the preprint DOI, finds
`published_doi`, creates a `version=published` row for the published DOI, and
updates the preprint row's `published_doi`.
Crossref batch mode finds real `preprints` rows with no `published_doi`,
looks them up in Crossref, creates any matched `version=published` rows, and
backfills the original preprint rows:

```bash
python3 -m ingestion.run_pull \
  --source crossref-batch \
  --batch-source all \
  --limit 25 \
  --apply
```

bioRxiv and medRxiv pulls page through the API in 100-record cursor steps until
`--limit` is reached or the date range is exhausted. Elasticsearch writes are
split into bulk requests controlled by `--bulk-batch-size` (default: 500):

```bash
python3 -m ingestion.run_pull \
  --source biorxiv \
  --since 2023-01-01 \
  --limit 500 \
  --include-published \
  --bulk-batch-size 250 \
  --apply
```

OpenAlex remains a dry-run lookup because Citation Finder now calls the
`openalex_citing_works` Elastic Workflow tool directly.

## Real Pair For Dispatcher

Dispatcher Step 8 needs one real pair in `preprints`: the preprint row and the
published row. Run the source pull first, then the Crossref pairing update:

```bash
python3 -m ingestion.run_pull \
  --source medrxiv \
  --since 2024-05-01 \
  --limit 5 \
  --include-published \
  --apply

python3 -m ingestion.run_pull \
  --source crossref \
  --doi 10.1101/2024.03.28.24304905 \
  --preprint-source medrxiv \
  --apply
```

Then dispatch with:

```json
{
  "preprint_doi": "10.1101/2024.03.28.24304905",
  "published_doi": "10.1186/s13073-024-01380-x"
}
```

The mappings use `semantic_text` fields with an explicit `.elser-2-elastic` inference endpoint for ELSER on Elastic Serverless. Do not attach an ingest inference pipeline that writes back into the same `semantic_text` field.

## Cloud Run Job

Build and deploy a puller job from the repo root:

```bash
gcloud builds submit \
  --config ingestion/cloudbuild.yaml \
  --substitutions _IMAGE=us-central1-docker.pkg.dev/$GCP_PROJECT/claimdrift/ingestion-puller

gcloud run jobs create claimdrift-medrxiv-puller \
  --image us-central1-docker.pkg.dev/$GCP_PROJECT/claimdrift/ingestion-puller \
  --region us-central1 \
  --set-env-vars ELASTIC_ENDPOINT="$ELASTIC_ENDPOINT" \
  --set-secrets ELASTIC_API_KEY=elastic-api-key:latest \
  --args "--source,medrxiv,--since,2024-05-01,--limit,25,--include-published,--apply"
```

For Crossref pairing, create a second job with args like:

```bash
--args "--source,crossref-batch,--batch-source,all,--limit,25,--apply"
```

## Seed Demo Records To Elasticsearch

```bash
python3 elastic/scripts/seed_demo_cases.py
python3 elastic/scripts/seed_demo_to_es.py --apply
```

This writes the demo records under `elastic/demo_seed` into the six Elasticsearch indexes with stable document ids.
Seeded records are tagged with `record_source=demo_seed` so they can be filtered out of real-data views.
