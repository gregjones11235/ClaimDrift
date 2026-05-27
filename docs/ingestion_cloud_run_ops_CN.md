# ClaimDrift Ingestion Cloud Run 运维记录

更新时间：2026-05-26

这份文档记录 B 端 ingestion 在 Cloud Run、Cloud Scheduler 和
Elasticsearch backfill 之后的当前状态。

## 当前状态

- GCP project: `tensile-topic-496519-i1`
- Region: `us-central1`
- Elasticsearch endpoint: `https://claim-drift-e4bdf7.es.us-central1.gcp.elastic.cloud:443`
- Docker image: `us-central1-docker.pkg.dev/tensile-topic-496519-i1/claimdrift/ingestion-puller`
- Cloud Run jobs 使用的 Secret Manager key: `elastic-api-key`
- Scheduler service account: `claimdrift-scheduler@tensile-topic-496519-i1.iam.gserviceaccount.com`

## Elasticsearch Backfill 结果

最新验证结果：

- 真实 `preprints` 文档总数：`10,067`
- 真实 preprint/published pair 数：`2,229+`
- demo 数据通过 `must_not term record_source=demo_seed` 排除。

这已经满足 C 端 real e2e 所需的 B 端数据规模：

- 真实 preprints 目标：`>= 10,000`
- 真实 pair 目标：`>= 500`

## Cloud Run Jobs

当前部署了三个 ingestion jobs：

- `claimdrift-biorxiv-puller`
- `claimdrift-medrxiv-puller`
- `claimdrift-crossref-puller`

当前稳定运行参数：

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

这些是增量运行参数。不要把历史 backfill 参数留在 Scheduler 触发的 jobs
上，例如：

```text
--since=2023-01-01 --limit=4000
```

否则会每小时/每天重复跑历史数据，浪费云额度并不断刷新 `ingested_at`。

代码更新后，在 repo root 重新构建并更新三个共用镜像的 Cloud Run Jobs：

```bash
gcloud builds submit \
  --config ingestion/cloudbuild.yaml \
  --substitutions _IMAGE=us-central1-docker.pkg.dev/tensile-topic-496519-i1/claimdrift/ingestion-puller

gcloud run jobs update claimdrift-biorxiv-puller \
  --image=us-central1-docker.pkg.dev/tensile-topic-496519-i1/claimdrift/ingestion-puller \
  --region=us-central1

gcloud run jobs update claimdrift-medrxiv-puller \
  --image=us-central1-docker.pkg.dev/tensile-topic-496519-i1/claimdrift/ingestion-puller \
  --region=us-central1

gcloud run jobs update claimdrift-crossref-puller \
  --image=us-central1-docker.pkg.dev/tensile-topic-496519-i1/claimdrift/ingestion-puller \
  --region=us-central1
```

## Cloud Scheduler

三个 Scheduler jobs 已启用，时区使用 `America/New_York`：

- `claimdrift-biorxiv-daily`
- `claimdrift-medrxiv-daily`
- `claimdrift-crossref-daily`

名字里仍然带 `daily`，但按 contract 应该使用 hourly cadence。推荐错开
触发时间，避免三个 Cloud Run jobs 同时抢资源：

```text
bioRxiv:  0 * * * *
medRxiv: 10 * * * *
crossref: 30 * * * *
```

## 验证命令

查看 Scheduler：

```bash
gcloud scheduler jobs list --location=us-central1
```

查看 Cloud Run Job 参数：

```bash
gcloud run jobs describe claimdrift-biorxiv-puller --region=us-central1 --format=yaml | grep -A15 "args:"
gcloud run jobs describe claimdrift-medrxiv-puller --region=us-central1 --format=yaml | grep -A15 "args:"
gcloud run jobs describe claimdrift-crossref-puller --region=us-central1 --format=yaml | grep -A15 "args:"
```

统计真实 preprints：

```bash
curl -s -X POST "$ELASTIC_ENDPOINT/preprints/_count" \
  -H "Authorization: ApiKey $ELASTIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":{"bool":{"must_not":[{"term":{"record_source":"demo_seed"}}]}}}'
```

统计真实 preprint/published pairs：

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

按 source 聚合：

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

## 注意事项

- `bioRxiv` 和 `medRxiv` pullers 已经支持 cursor 分页。
- ES 写入已经支持 bulk 分批。
- CLI 默认只输出 summary，避免 Cloud Logging 被全文 abstract 淹没。
- 本地调试需要看 records 时可以加 `--include-items`。
- `crossref-batch` 每个候选 DOI 都要查一次 Crossref，所以 limit 应该比
  bioRxiv/medRxiv 小。
- arXiv 已经按 `docs/contracts.md` 从当前 ingestion scope 中移除。
- 后续工业化可以加入 checkpoint-based incremental ingestion，避免长期依赖
  静态 `--since` 窗口。
