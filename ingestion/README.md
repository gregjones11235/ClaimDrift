# ClaimDrift — Ingestion

This directory contains B-owned data ingestion code for ClaimDrift.

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
python3 -m ingestion.run_pull --source openalex --doi 10.1101/2024.01.21.24301585 --limit 5 --dry-run
```

## Write To Elasticsearch

```bash
export ELASTIC_ENDPOINT="https://your-cluster.example.com"
export ELASTIC_API_KEY="..."

python3 elastic/scripts/create_indices.py --apply
python3 -m ingestion.run_pull --source medrxiv --since 2024-05-01 --limit 3 --apply
```

`--apply` writes bioRxiv and medRxiv records to the `preprints` index. Crossref lookup remains dry-run only until the published-version pairing flow is wired in. OpenAlex can write `affected_citations` candidate records for a known drift event; Citation Finder still owns final severity scoring.

The mappings use `semantic_text` fields with an explicit `.elser-2-elastic` inference endpoint for ELSER on Elastic Serverless. Do not attach an ingest inference pipeline that writes back into the same `semantic_text` field.

## Seed Demo Records To Elasticsearch

```bash
python3 elastic/scripts/seed_demo_cases.py
python3 elastic/scripts/seed_demo_to_es.py --apply
```

This writes the demo records under `elastic/demo_seed` into the six Elasticsearch indexes with stable document ids.
Seeded records are tagged with `record_source=demo_seed` so they can be filtered out of real-data views.

## OpenAlex Candidate Writes

OpenAlex can write affected-citation candidates for a known drift event:

```bash
python3 -m ingestion.run_pull \
  --source openalex \
  --doi 10.1038/nature12373 \
  --drift-event-id demo-drift-001 \
  --limit 3 \
  --apply
```

These records are tagged with `record_source=openalex_candidate` and `severity_tier=pending`. Citation Finder still owns final severity scoring.
