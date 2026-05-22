# ClaimDrift — Ingestion

This directory contains B-owned data ingestion code for ClaimDrift.

It currently supports:

- bioRxiv pulls
- medRxiv pulls
- Crossref DOI lookup
- Contract-shaped dry-run output
- Optional Elasticsearch bulk upsert for `preprints`

## Dry Run

```bash
python3 -m ingestion.run_pull --source medrxiv --since 2024-05-01 --limit 3 --dry-run
python3 -m ingestion.run_pull --source biorxiv --since 2024-05-01 --limit 3 --dry-run
python3 -m ingestion.run_pull --source crossref --doi 10.1101/2024.01.15.123456 --dry-run
```

## Write To Elasticsearch

```bash
export ELASTIC_ENDPOINT="https://your-cluster.example.com"
export ELASTIC_API_KEY="..."
# Legacy names (ELASTICSEARCH_URL / ELASTICSEARCH_API_KEY) are still honored as a fallback.

python3 elastic/scripts/create_indices.py --apply
python3 -m ingestion.run_pull --source medrxiv --since 2024-05-01 --limit 3 --apply
```

`--apply` currently writes bioRxiv and medRxiv records to the `preprints` index. Crossref lookup is dry-run only until the published-version pairing flow is wired in.
