# ClaimDrift JSON 构建说明

这个项目里的 JSON 不是同一种东西。它们分成三层：

1. 契约 JSON：描述系统应该长什么样。
2. 配置 JSON：告诉 Elasticsearch 怎么建 index 和 ingest pipeline。
3. Demo 数据 JSON：模拟 agent 和 puller 跑完后会写出的业务数据。

## 1. Source of truth

最上游是 `docs/contracts.md` 和 `docs/contracts_CN.md`。

这两个文档定义了：

- 需要哪些 Elasticsearch index。
- 每个 index 有哪些字段。
- 字段类型和语义是什么。
- 每个组件读写哪些 index。
- SSE 事件和 agent 输入输出大概是什么结构。

所以 JSON 的建造顺序不是从代码随便写起，而是：

```text
contract 文档
  -> elastic/mappings/*.json
  -> elastic/pipelines/elser_ingest_pipeline.json
  -> elastic/demo_seed/*.json
  -> BFF API response
  -> frontend TypeScript types
```

## 2. Mapping JSON 是怎么来的

`elastic/mappings/*.json` 是 Elasticsearch index schema。

这些文件目前是手写的，依据来自 contract 文档里的最小字段约束。比如 `preprints` contract 说：

- `doi` 要精确匹配，所以用 `keyword`
- `title` 需要全文搜索，也可能精确聚合，所以用 `text` + `keyword` 子字段
- `abstract` 要做语义检索，所以用 `semantic_text`
- `authors` 是一组结构化对象，所以用 `nested`
- `posted_date` 和 `ingested_at` 是时间，所以用 `date`

于是落到 `elastic/mappings/preprints.json` 就变成：

```json
{
  "settings": {
    "index.default_pipeline": "claimdrift_elser_v1",
    "refresh_interval": "1s"
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "doi": { "type": "keyword" },
      "title": {
        "type": "text",
        "fields": { "keyword": { "type": "keyword", "ignore_above": 512 } }
      },
      "abstract": { "type": "semantic_text" }
    }
  }
}
```

这里的 `dynamic: strict` 很重要：它会阻止未知字段悄悄写进 ES，方便早期发现 contract drift。

## 3. ELSER pipeline JSON 是怎么来的

`elastic/pipelines/elser_ingest_pipeline.json` 是 ingest pipeline 配置。

它的作用是：当文档写入带有 `index.default_pipeline = claimdrift_elser_v1` 的 index 时，把指定文本字段交给 ELSER 模型做 inference。

当前接入的语义字段是：

- `preprints.abstract`
- `preprints.conclusion`
- `claims.text`
- `drift_patterns.pattern_description`

这些字段对应 memory loop 和语义检索的核心路径。

## 4. Demo seed JSON 是怎么来的

`elastic/demo_seed/*.json` 不是手写主入口，它们由脚本生成：

```bash
python3 elastic/scripts/seed_demo_cases.py
```

生成脚本在内存里先构造 Python list/dict：

```python
preprints = [
    {
        "doi": "...",
        "source": "biorxiv",
        "abstract": "...",
    }
]
```

然后用：

```python
json.dumps(rows, indent=2)
```

写成格式化后的 JSON 文件。

这样做的好处是：

- 时间戳可以统一生成。
- 多个 index 之间的 id 可以互相对齐。
- 可以一次生成完整 demo 链路，而不是手动维护六个容易错位的 JSON 文件。

## 5. 一条 demo 数据如何流动

以 `demo-drift-001` 为例：

```text
preprints.json
  final preprint + published version
        |
claims.json
  两边各抽出一个 claim
        |
drift_events.json
  记录 claim diff、materiality score、retrieved pattern
        |
affected_citations.json
  记录哪个下游 citing paper 受影响
        |
notification_log.json
  记录 notifier 草稿和发送状态
        |
drift_patterns.json
  记录 memory loop 可以复用的 pattern
```

mock BFF 不重新计算这些东西，它只是读取 `elastic/demo_seed/*.json`，然后按 API 路径返回给前端。

## 6. 以后哪些 JSON 应该由程序生成

短期可以继续手写：

- `elastic/mappings/*.json`
- `elastic/pipelines/elser_ingest_pipeline.json`

应该由脚本生成或外部系统写入：

- `elastic/demo_seed/*.json`
- 真实环境里的 ES documents
- Agent Builder 的输出记录
- Notifier 的 `notification_log`

下一步比较自然的是把 `create_indices.py` 从 dry-run 变成真实执行脚本，让这些 mapping/pipeline JSON 可以一键写入 Elasticsearch。
