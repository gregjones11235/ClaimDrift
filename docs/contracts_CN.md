# ClaimDrift — Inter-component Contracts

> **状态**: v0 (Day 1-2)
> **范围**: 跨组件契约。组件内部细节(具体 prompt、前端组件、puller 实现)在各自的 repo / 文件里,不放这里。

---

## 关于本文档

**两点说明,阅读前先看**:

1. **本文档既给人读、也给机器用**。散文部分(职责描述、设计决策、字段语义)给团队成员对齐理解;代码块部分(JSON schema、ES mapping、SSE 事件格式)是 source of truth,可以直接复制粘贴到 Agent Builder 的 tool 定义、ES index mapping、前端的 TypeScript 类型里。所以请保持代码块字段名、类型严格准确,描述性段落可以适度宽松。

2. **本文档只覆盖跨组件接口,不代表全部工作量**。每个人的主要工作量都在自己的 repo / 工具里(C 在 Agent Builder + Cloud Run, B 在 puller 实现 + ES 配置, A 在 Google AI Studio 写 prompt, D 在前端 repo)。文档里 TODO 数量不等于工作量分布,详见下方「团队分工速览」。

---

## 团队分工速览

四人分工。本文档仅覆盖跨组件接口,真正的工作大头在各自的 repo / 工具里。

**A (Prompt 工程 + 生物医学领域)**:  //Taiyang

- 5 个 Gemini agent 的 prompt(在 Google AI Studio 里迭代,存进 `prompts/` 目录)
- 5-10 个 demo-grade drift case 的人工 ground truth 标注
- 邮件 tone 校准的示例
- 视频 / Devpost 文案里的领域素材

**B (全栈工程师, 数据 + 后端)**:  //Jeremy

- Ingestion pipeline 的 4 个 Python puller(Cloud Run Jobs + Cloud Scheduler)
- 6 张 Elasticsearch index 的完整 mapping(本文档 §2 的 TODO B)
- Elastic Ingest Pipeline + ELSER 配置
- 前端 BFF + Server-Sent Events 通道
- Notifier 的 email dispatch 代码

**C (架构 + GCP 总负责)**:  //Jiayu Zhu (Alec)

- Google Cloud Agent Builder 里真的把 5 个 agent 搭起来、wire tools
- Elastic MCP server 配置(让 Agent Builder 能调 ES|QL)
- Elastic Workflows 的实际编排
- **Memory loop 的实现和调优**(项目最大卖点)
- Cloud Run 部署 + 整体 E2E 调试

**D (前端 + Demo 视频 + 对外包装)**:  //tty (Ranjan)

- Next.js + Tailwind + shadcn/ui 整套前端,部署 Vercel(6 个 view)
- 3 分钟 demo 视频(脚本、屏录、配音、剪辑)
- Devpost 提交页文案 + README 包装
- Agent Builder 配置 runbook(防止 Agent Builder 新产品的 API/quota 变动)

---

## 0. 文档约定

- 标 `TODO A/B/C/D` 的项: 由 A/B/C/D 完成
- 标 `TODO A (feedback)`的项: 反馈型钩子, 跑过真实数据后如果发现问题就反馈
- 标 `暂定` 的字段: 可能会在后续修改
- 所有 JSON 示例里 `// 注释` 都是文档注释,实际数据里不出现

---

## 1. 系统骨架

ClaimDrift 由 **1 个 ingestion pipeline + 5 个 Gemini agent** 组成,统一通过 Elastic Workflows 编排,通过 Elastic MCP server 调用 ES|QL 工具。

### 1.1 组件职责一句话总结

| 组件 | 职责 |
|------|------|
| **Ingestion Pipeline** (非 agent) | 从 arXiv/bioRxiv/medRxiv/Crossref/OpenAlex 拉数据,写入 `preprints` index |
| **Claim Extractor** | 把每个 preprint version 拆成结构化 claims,写入 `claims` |
| **Drift Analyzer** | 比对 v-final-preprint ↔ published 的 claim sets,产出 drift report,写入 `drift_events` |
| **Citation Finder** | 找出引用了 drifted preprint 的下游论文,打 severity,写入 `affected_citations` |
| **Notifier** | 给每个 affected citation 生成邮件 draft,发送(测试邮箱),写入 `notification_log` |
| **Memory Synthesizer** | 把 drift events 蒸馏成可复用 patterns,写入 `drift_patterns` |

### 1.2 数据流方向

```
   arXiv/bioRxiv/medRxiv/Crossref/OpenAlex
                  ↓ (pull)
              [Ingestion]
                  ↓
              preprints ─────┐
                  ↓          │
          [Claim Extractor]  │
                  ↓          │
                claims ──────┤
                  ↓          │
          [Drift Analyzer] ←─┤  (读 drift_patterns 做条件)
                  ↓          │
             drift_events ───┤
                  ↓          │
          [Citation Finder]  │
                  ↓          │
          affected_citations │
                  ↓          │
              [Notifier]     │
                  ↓          │
           notification_log  │
                             │
                             ↓
                    [Memory Synthesizer] (异步)
                             ↓
                       drift_patterns ←──┐
                                         │ (Drift Analyzer 下次读)
                                         │
```

### 1.3 设计决策

- **Claim 抽取粒度**: 句子级
- **Drift Analyzer 比对范围**: 只做 v-final-preprint ↔ published-journal(不做 v1↔v-final 的 preprint 内部 diff,数据最干净)
- **Citation Finder**: 只用 OpenAlex citation edge（引用边，代表论文之间的引用关系）,不抓 citing paper 的 PDF
- **Notifier**: 一篇 affected paper 一封邮件(不按作者批合并（假设一个作者有三篇受影响的论文，会分别发送三封邮件通知，而不是合并成一封邮件），避免做跨论文聚合)
- **Demo 一共包含2个Drift事件演示**: 第二个理论上会比第一个更好，展示出memory loop的能力
- **Zotero, Mendeley API通知**: 通过 Zotero, Mendeley API在用户的参考文献管理器里给发生drift的论文加标签

---

## 2. Elasticsearch Index 清单

### 2.1 Index 总览表

| Index 名 | 用途 | _id | 写入方 | 读取方 |
|---------|------|-----|--------|--------|
| `preprints` | 所有 preprint 的 metadata + abstract | DOI (URL-decoded) | Ingestion | Claim Extractor, Drift Analyzer, 前端 |
| `claims` | 每个 preprint version 抽出的 claim 列表 | `{doi}::{version}::{claim_idx}` | Claim Extractor | Drift Analyzer, 前端 |
| `drift_events` | 每次 drift 检测产生的报告 | UUID (自动生成) | Drift Analyzer | Citation Finder, Memory Synthesizer, 前端 |
| `affected_citations` | drift 影响的下游 citing paper | `{drift_event_id}::{citing_doi}` | Citation Finder | Notifier, Memory Synthesizer, 前端 |
| `drift_patterns` | 蒸馏后的可复用 drift pattern | UUID | Memory Synthesizer | **Drift Analyzer (memory loop 核心)**, 前端 |
| `notification_log` | 邮件 draft + 发送状态 | `{affected_citation_id}` | Notifier | 前端 |

**TODO D (Day 5-7)**: 前端实际渲染时如果发现某个 index 不需要、或者需要新的聚合视图(比如按作者聚合的 affected_citations 视图)、或者读取字段不够,在群里 @ B 和 C。

### 2.2 Mapping 详细定义

每个 index 的 mapping 由 B 填充。下面给出最小字段约束 —— B 在此基础上加 analyzer、index options、ELSER 语义检索接入点。

#### 2.2.1 `preprints` index

**最小字段约束** (B 必须包含):

- `doi`: keyword
- `source`: keyword (`arxiv` | `biorxiv` | `medrxiv`)
- `version`: keyword (例如 `v1`, `v2`)
- `is_final_preprint`: boolean
- `published_doi`: keyword | null (Crossref 给出的最终 published DOI, null 表示还没发表)
- `title`: text + keyword 子字段
- `abstract`: semantic_text (走 ELSER)
- `conclusion`: semantic_text | null
- `authors`: nested,包含 `name` (keyword), `orcid` (keyword | null), `affiliation` (text)
- `posted_date`: date (ISO 8601)
- `ingested_at`: date

**TODO B (Day 3-4)**: 完整 mapping JSON,包括 ELSER inference endpoint、shards/replicas、refresh interval。

```json
// TODO B: 填入完整 mapping JSON
{
  "mappings": { ... },
  "settings": { ... }
}
```

#### 2.2.2 `claims` index

**最小字段约束**:

- `claim_id`: keyword (= _id, 形式 `{doi}::{version}::{claim_idx}`)
- `parent_doi`: keyword
- `parent_version`: keyword
- `section`: keyword (`abstract` | `conclusion`)
- `claim_idx`: integer (0-based, 在该 section 内的序号)
- `text`: semantic_text (走 ELSER)
- `claim_type`: keyword (取值见 §3.1.2)
- `numerical_values`: nested | null (有数值时填,结构见 §3.1.2)
- `hedging_level`: keyword (`none` | `weak` | `strong`)  //指作者为了表达谨慎、不确定性或限制结论的适用范围而使用的委婉语气程度
- `extracted_at`: date

**TODO B (Day 3-4)**: 完整 mapping JSON。

#### 2.2.3 `drift_events` index

**最小字段约束**:

- `event_id`: keyword (UUID)
- `preprint_doi`: keyword
- `preprint_version_compared`: keyword (是哪个 version 和 published 比的)
- `published_doi`: keyword
- `detected_at`: date
- `drift_summary`: text (Gemini 生成的人读总结)
- `claim_diffs`: nested,结构见 §3.2.2
- `materiality_score`: float (0.0-1.0, 表示这次 drift 整体的"严重度")
- `retrieved_patterns`: nested | null (从 drift_patterns 检索到的、本次用了的 pattern, 结构见 §3.2.2)

**TODO B (Day 3-4)**: 完整 mapping JSON。

#### 2.2.4 `affected_citations` index

**最小字段约束**:

- `affected_citation_id`: keyword (= _id, `{drift_event_id}::{citing_doi}`)
- `drift_event_id`: keyword
- `citing_paper_doi`: keyword
- `citing_paper_title`: text
- `citing_paper_authors`: nested (name, orcid, email | null)
- `citation_context`: text | null (如果 OpenAlex 提供了上下文; 大多数情况下是 null,因为我们不抓 PDF)
- `severity_tier`: keyword (`central` | `comparative` | `peripheral`)
- `severity_reasoning`: text (Gemini 的判断依据)
- `scored_at`: date

**TODO B (Day 3-4)**: 完整 mapping JSON。

#### 2.2.5 `drift_patterns` index ⭐

> 这是 memory loop 的核心 index,字段设计要让 Drift Analyzer 能高效检索。

**最小字段约束**:

- `pattern_id`: keyword (UUID)
- `pattern_description`: semantic_text (人读 + ELSER 检索, **是 memory loop 的检索字段**)
- `pattern_type`: keyword (`numerical_softening` | `hedging_addition` | `claim_disappearance` | `effect_size_reduction` | `other`) // TODO A: 在 Memory Synthesizer prompt 中迭代后可能扩充
- `domain_tags`: keyword (array, 例如 `["covid-19", "clinical-trial", "rct"]`)
- `source_event_ids`: keyword (array, 衍生这个 pattern 的 drift_event 列表)
- `support_count`: integer (这个 pattern 被多少 drift_event 支撑过, 越多越可信)
- `created_at`: date
- `last_updated_at`: date

**TODO B (Day 3-4)**: 完整 mapping JSON。**特别注意**: `pattern_description` 必须接 ELSER 语义检索, 因为 Drift Analyzer 的 retrieval 依赖它。

#### 2.2.6 `notification_log` index

**最小字段约束**:

- `affected_citation_id`: keyword (= _id)
- `drift_event_id`: keyword
- `recipient_email`: keyword
- `subject`: text
- `body`: text
- `status`: keyword (`drafted` | `sent` | `bounced` | `failed`)
- `sent_at`: date | null
- `error_message`: text | null

**TODO B (Day 3-4)**: 完整 mapping JSON。

---

## 3. Agent 输入/输出 JSON 骨架

所有 agent 的 input/output 都是 JSON。命名规范见 §7。Agent 实际通过 Elastic Workflows 串联,但每个 agent 在 Agent Builder 里都独立定义 input/output schema,以便单独测试。

### 3.1 Claim Extractor (Agent 1)

#### 3.1.1 Input

```json
{
  "preprint_doi": "10.1101/2024.01.15.123456",        // 必填
  "version": "v3",                                     // 必填
  "title": "...",                                      // 必填, 从 preprints index 读
  "abstract": "...",                                   // 必填
  "conclusion": "..."                                  // 可选, null 表示没有 conclusion section
}
```

#### 3.1.2 Output

```json
{
  "preprint_doi": "10.1101/2024.01.15.123456",
  "version": "v3",
  "claims": [
    {
      "section": "abstract",                           // "abstract" | "conclusion"
      "claim_idx": 0,                                  // 在该 section 内 0-based
      "text": "Hydroxychloroquine reduced viral load by 45% in COVID-19 patients.",
      "claim_type": "quantitative",                    // 见下方枚举
      "numerical_values": [                            // 可选, 仅当 claim 包含数值时
        {
          "metric": "viral_load_reduction",            // 暂定: 字段名由 A 在 prompt 中定义
          "value": 45.0,
          "unit": "percent",
          "comparison": "reduction"                    // "reduction" | "increase" | "ratio" | "absolute"
        }
      ],
      "hedging_level": "none"                          // "none" | "weak" | "strong"
    }
  ],
  "extraction_metadata": {
    "model": "gemini-2.x-...",                         // TODO C: 填入实际模型名
    "extracted_at": "2026-05-20T12:34:56Z"
  }
}
```

**`claim_type` 枚举** (暂定,A 可在 prompt 中调整):

| 值 | 含义 |
|----|------|
| `qualitative` | 定性结论,无数值。"X improves Y" |
| `quantitative` | 含数值的结论。"X reduced Y by 45%" |
| `causal` | 因果断言。"X causes Y" |
| `correlational` | 相关性。"X is associated with Y" |
| `hedged` | 显式带 hedge 的。"X may improve Y" |

注: 一个 claim 可以同时是 `quantitative` + `causal`,但 v0 我们只取最主要的一个 tag。如果 A 在 prompt 调试中发现需要多 tag,改成 array 即可(改字段类型 → 走 §8 的演进规则)。

**TODO A**: prompt 调试时如果发现 `numerical_values` 的子字段不够用(例如需要 `confidence_interval`),在群里反馈。

### 3.2 Drift Analyzer (Agent 2)

#### 3.2.1 Input

```json
{
  "preprint_doi": "10.1101/2024.01.15.123456",
  "preprint_version_compared": "v3",                   // 通常是 final preprint version
  "published_doi": "10.1016/j.cell.2024.05.001",
  "preprint_claims": [ /* 来自 claims index, claim 对象数组 */ ],
  "published_claims": [ /* 来自 claims index, claim 对象数组 */ ],
  "retrieved_patterns": [                              // memory loop 读侧, 可能为空数组
    {
      "pattern_id": "...",
      "pattern_description": "...",
      "pattern_type": "...",
      "domain_tags": [ "..." ],
      "support_count": 5,
      "similarity_score": 0.82                          // ELSER 检索分数
    }
  ]
}
```

**retrieval 规则** (memory loop 关键):

- Drift Analyzer 在被调用前,先用 preprint 的 abstract 在 `drift_patterns` 里做 hybrid search (ELSER + BM25)
- 取 top-3 results,过滤 `similarity_score >= 0.7`
- 过滤后的 pattern 作为 `retrieved_patterns` 注入
- 如果一个都没有,传空数组,Drift Analyzer 照常工作

阈值 `0.7` 是 v0 拍的,Week 2 跑通后看效果调整。

#### 3.2.2 Output

```json
{
  "event_id": "uuid-v4-here",
  "preprint_doi": "...",
  "preprint_version_compared": "v3",
  "published_doi": "...",
  "drift_summary": "Effect size for HCQ on viral load was reduced from 45% to 12%, and a hedging qualifier was added in the published version.",
  "claim_diffs": [
    {
      "diff_type": "numerical_shift",                  // 见下方枚举
      "preprint_claim_id": "10.1101/...::v3::abstract::0",
      "published_claim_id": "10.1016/...::v1::abstract::0",
      "preprint_text": "...",
      "published_text": "...",
      "change_description": "Effect size reduced from 45% to 12%",
      "numerical_delta": {                             // 可选, 仅当 diff_type 是 numerical_shift
        "metric": "viral_load_reduction",
        "preprint_value": 45.0,
        "published_value": 12.0,
        "absolute_delta": -33.0,
        "relative_delta": -0.733                       // -73.3%
      }
    }
  ],
  "materiality_score": 0.82,                           // 0.0-1.0, 整体严重度
  "retrieved_patterns_used": [                         // 哪些 retrieved patterns 实际进入了 reasoning
    "pattern-uuid-1",
    "pattern-uuid-2"
  ],
  "analyzed_at": "2026-05-20T..."
}
```

**`diff_type` 枚举** (暂定):

| 值 | 含义 |
|----|------|
| `claim_disappeared` | preprint 里有的 claim 在 published 里完全没有了 |
| `claim_added` | published 里新增的 claim |
| `numerical_shift` | 同一 claim 的数值变化 |
| `hedging_added` | published 版本增加了 hedge 词 |
| `hedging_removed` | published 版本去掉了 hedge 词(很罕见) |
| `claim_reversed` | 结论方向反转(最严重) |

**materiality_score 评分指引** (暂定,给 A 写 prompt 时参考):

- `0.0-0.3`: 微小调整(措辞变化、数值小数点修正)
- `0.3-0.6`: 中等(数值变化 < 50%、增加了 hedging)
- `0.6-0.9`: 显著(数值变化 > 50%、claim 消失)
- `0.9-1.0`: 重大(结论反转、显著性消失)

**TODO A (feedback)**: 跑过几个真实 case 后,如果发现这些阈值不合理(比如真实数据里大部分 case 都落在 0.3-0.6 区间,区分度不够),在群里反馈,C 会调整。

### 3.3 Citation Finder (Agent 3)

#### 3.3.1 Input

```json
{
  "drift_event_id": "uuid-...",
  "preprint_doi": "10.1101/...",                       // 要找谁引用了它
  "drift_summary": "...",                              // 复制自 drift_event, 让 Gemini 判断 severity 用
  "claim_diffs": [ /* 复制自 drift_event */ ]
}
```

#### 3.3.2 Output

```json
{
  "drift_event_id": "uuid-...",
  "affected_citations": [
    {
      "citing_paper_doi": "10.1038/...",
      "citing_paper_title": "...",
      "citing_paper_authors": [
        {
          "name": "Jane Doe",
          "orcid": "0000-0000-0000-0000",
          "email": "jane@example.edu"                   // null 也行,demo 用 team 测试邮箱
        }
      ],
      "citation_context": null,                         // 几乎总是 null, OpenAlex 通常不给
      "severity_tier": "central",                       // "central" | "comparative" | "peripheral"
      "severity_reasoning": "The citing paper builds its main conclusion on the 45% effect size, which has now been revised to 12%. This invalidates their primary argument."
    }
  ],
  "total_found": 47,                                    // 总共找到多少 citing papers
  "processed": 47,                                      // 实际处理了多少(可能因为限流跳过一些)
  "found_at": "2026-05-20T..."
}
```

**`severity_tier` 判定指引**(给 A 写 prompt 时参考):

- `central`: drifted claim 是 citing paper 的核心论据
- `comparative`: drifted claim 被 citing paper 用来对比/参考,不是核心
- `peripheral`: drifted claim 只在 related work 里被提一句

**TODO A (feedback)**: 跑过 demo case 后,如果发现三档不够用(比如想加一档 "background"),或者判定指引让 Gemini 分不清,在群里反馈。

### 3.4 Notifier (Agent 4)

#### 3.4.1 Input

```json
{
  "affected_citation_id": "...",
  "drift_event_summary": "...",                         // 从 drift_event 拿
  "claim_diffs": [ /* drifted claim 列表 */ ],
  "citing_paper_doi": "...",
  "citing_paper_title": "...",
  "recipient": {
    "name": "Jane Doe",
    "email": "test+jane@team-mailbox.com",              // demo 阶段统一发到 team 测试邮箱
    "is_first_author": true
  },
  "severity_tier": "central",
  "severity_reasoning": "..."
}
```

#### 3.4.2 Output

```json
{
  "affected_citation_id": "...",
  "subject": "Update on preprint cited in your paper: HCQ viral load study",
  "body": "Dear Dr. Doe,\n\nWe noticed that your paper '...' (DOI: ...) cites preprint ...\n\n...",
  "reasoning_trace": "Generated personalized notification based on...",
  "drafted_at": "2026-05-20T...",
  "dispatch": {                                         // Notifier 同步触发发送, 这里记录结果
    "status": "sent",                                   // "sent" | "bounced" | "failed" | "skipped"
    "sent_at": "2026-05-20T...",
    "error_message": null
  }
}
```

**邮件 tone 指引** (给 A 写 prompt 时参考):

- 中性、信息性,**不带说教或责备**
- 直接引用 drifted claim 原文 + published 版本原文 + 链接
- 说明 severity 和原因
- 明确 disclaimer: 这是自动化系统检测的提醒,作者自行判断是否需要更新

### 3.5 Memory Synthesizer (Agent 5) ⭐

> Memory loop 的写侧。这个 agent 异步触发(在 drift_event 写完之后),不阻塞主流程。

#### 3.5.1 Input

```json
{
  "trigger": "new_drift_event",                          // 触发原因
  "drift_event_id": "uuid-...",
  "drift_event": { /* 完整 drift_event */ },
  "affected_citations_summary": {                        // 聚合信息, 不传完整列表
    "total_affected": 47,
    "central_count": 8,
    "comparative_count": 21,
    "peripheral_count": 18
  },
  "existing_similar_patterns": [                         // 可能为空, 用来决定是新建还是更新
    {
      "pattern_id": "...",
      "pattern_description": "...",
      "similarity_score": 0.78
    }
  ]
}
```

**existing_similar_patterns retrieval 规则**:

- Memory Synthesizer 触发前,先用 drift_event 的 `drift_summary` 在 `drift_patterns` 里检索
- 取 top-5,过滤 `similarity_score >= 0.75`
- 如果有匹配的,Memory Synthesizer **更新** 现有 pattern 的 `support_count` 而不是新建
- 如果没有,**新建** 一个 pattern

#### 3.5.2 Output

```json
{
  "action": "create_new",                                // "create_new" | "update_existing"
  "pattern": {
    "pattern_id": "uuid-...",                            // 新建时新生成, 更新时复用旧 id
    "pattern_description": "COVID-related clinical preprints frequently show 50%+ reduction in reported effect size between final preprint and published version, often with added hedging language.",
    "pattern_type": "effect_size_reduction",
    "domain_tags": ["covid-19", "clinical-trial"],
    "source_event_ids": ["uuid-..."],                    // 新建时只有这一个; 更新时 append
    "support_count": 1,                                  // 新建时 1; 更新时 +1
    "created_at": "2026-05-20T...",
    "last_updated_at": "2026-05-20T..."
  },
  "synthesized_at": "2026-05-20T..."
}
```

**pattern_description 写作指引** (给 A 写 prompt 时参考):

- 必须是**可复用**的总结,不要只描述这一次事件
- 必须包含: 哪个领域 + 什么类型的 drift + 大致量级
- 不能包含具体 DOI 或作者名(那是 source_event_ids 的事)
- 30-80 字英文,后续 Drift Analyzer 检索时会拿来注入 prompt context

---

## 4. Agent 调用顺序和编排

### 4.1 主流程 (同步)

```
[Ingestion 完成, preprints index 新增/更新一条]
    ↓
trigger: 检测到一个 preprint 有 published_doi
    ↓
[Claim Extractor]  执行两次, 分别针对 preprint final version 和 published version
    ↓
[Drift Analyzer]  retrieval-then-reason
    ↓
[Citation Finder]
    ↓
[Notifier]  对每个 affected citation 触发一次
    ↓
完成
```

### 4.2 副流程 (异步)

```
[Drift Analyzer 完成, drift_events 新增一条]
    ↓ (异步触发, 不阻塞)
[Memory Synthesizer]
    ↓
drift_patterns 新增/更新
```

### 4.3 Workflow 实现

由 Elastic Workflows 编排。TODO C (Day 8-10): 在 workflow YAML / 配置里实现上述拓扑,并在 README 中给出 workflow 文件路径。

---

## 5. Ingestion Pipeline 接口

每个 puller 的职责是: 拉取数据 → 标准化 → bulk write 进 `preprints` index (符合 §2.2.1)。

### 5.1 Puller 清单

| Puller | Source | 触发方式 | 输出 index |
|--------|--------|---------|-----------|
| `arxiv_puller` | arXiv OAI-PMH | Cloud Scheduler, 每日 | `preprints` |
| `biorxiv_puller` | bioRxiv REST | Cloud Scheduler, 每小时 | `preprints` |
| `medrxiv_puller` | medRxiv REST | Cloud Scheduler, 每小时 | `preprints` |
| `crossref_puller` | Crossref Event Data webhook + REST fallback | Webhook + Cloud Scheduler 兜底 | 更新 `preprints` 的 `published_doi` 字段 |
| `openalex_puller` | OpenAlex REST | 按需(被 Citation Finder 触发) | (不直接写 ES, 返回给 agent) |

### 5.2 通用规则 (B 必须遵守)

- 所有出站请求带 polite-pool header (`User-Agent` 含 contact email)
- arXiv 速率限制 ≥3 秒/请求
- 写入 ES 用 bulk API
- DOI 标准化: 小写,不带 `https://doi.org/` 前缀
- 写入是 upsert (DOI 已存在则更新)

**TODO B (Day 3-5)**: 每个 puller 的实现细节(batch size、错误处理、retry 策略、日志格式)。

### 5.3 ELSER 语义检索接入

`preprints`、`claims` 和 `drift_patterns` 的 semantic_text 字段必须接 ELSER 语义检索。

**Serverless 实现说明 (B)**: 使用 `semantic_text` 并显式指定 `.elser-2-elastic` `inference_id`。不要挂一个 ingest inference pipeline 把 ELSER 输出写回同一个 `semantic_text` 字段,因为 `semantic_text` 写入时期望文档字段保持为原始文本标量。

---

## 6. BFF / Server-Sent Events 事件格式

前端通过 SSE 从 BFF 接收实时 agent 状态。事件由 agent 在 Agent Builder 里 emit,BFF 转发。

### 6.1 事件类型清单 (C 起草, D 反馈)

事件由 agent 在 Agent Builder 里 emit, C 在配置 agent 时决定 emit 哪些; D 是前端消费方, 对事件粒度和 payload 内容有最终发言权。

所有事件都包含公共字段:

```json
{
  "event_type": "...",
  "agent_id": "claim_extractor" | "drift_analyzer" | "citation_finder" | "notifier" | "memory_synthesizer",
  "drift_event_id": "uuid-..." | null,                   // 串联同一个 drift event 的所有事件
  "timestamp": "2026-05-20T...",
  "payload": { /* 见各事件类型 */ }
}
```

事件类型:

| event_type | 何时 emit | payload |
|-----------|----------|---------|
| `agent.started` | agent 开始执行 | `{ input_summary }` |
| `agent.tool_call` | agent 调用 ES|QL 工具 | `{ tool_name, args }` |
| `agent.pattern_retrieved` | **Drift Analyzer 检索到 pattern (memory loop 关键事件, 前端要高亮)** | `{ pattern_ids, similarity_scores }` |
| `agent.step` | agent 完成一个推理步骤 | `{ step_name, summary }` |
| `agent.completed` | agent 执行完成 | `{ output_summary, output_id }` |
| `agent.failed` | agent 出错 | `{ error_message, retry_count }` |

**TODO D (Day 8-10)**: 前端做 agent activity timeline 时,如果发现:

- 缺事件类型(例如 "agent 暂停等待 retrieval 结果" 这种中间态)
- payload 信息不够(例如 `agent.completed` 想看到具体输出片段而不只是 summary)
- 想要某些事件的额外字段(例如 `agent.pattern_retrieved` 想看到完整 pattern_description 而不只是 id)

在群里 @ C, C 在 Agent Builder 里调整 emit 配置。

### 6.2 SSE 传输细节

**TODO B (Day 5-6)**: SSE channel 设计、心跳间隔、断线重连协议、前端订阅方式。

---

## 7. 全局命名 + 编码规范

### 7.1 命名

- **字段名**: snake_case (例如 `preprint_doi`, 不用 `preprintDoi`)
- **enum 值**: 小写带下划线的字符串 (例如 `numerical_shift`, 不用 `NUMERICAL_SHIFT` 或 `numericalShift`)
- **Index 名**: 复数,小写,下划线 (例如 `drift_events`, 不用 `DriftEvent`)
- **Agent ID**: 小写下划线 (例如 `drift_analyzer`)

### 7.2 编码

- **时间**: ISO 8601 UTC,带 Z 后缀 (`2026-05-20T12:34:56Z`),不用本地时区
- **DOI**: 标准化为不带 `https://doi.org/` 前缀的纯路径,小写。例: `10.1101/2024.01.15.123456`
- **可空字段**: 显式 `null`,不要用空字符串
- **ID**: UUID v4 (除非有自然主键)
- **JSON**: 不带 trailing comma,字符串用双引号

### 7.3 字段类型约定

- 整数计数用 `integer`,不用 `long` (我们的量级用不到)
- 分数和概率用 `float`,范围 0.0-1.0
- 自由文本用 `text`;需要精确匹配的短字符串用 `keyword`;需要语义检索的用 `semantic_text`

---

## 8. 接口演进规则

骨架定型后,字段细节会一直演进。下面的规则保证演进不混乱。

### 8.1 改动分类

| 改动类型 | 频率(预期) | 流程 |
|---------|----------|------|
| **加新字段** | 高 (~70%) | 谁需要谁加, commit + 群里说一声, 在本文档对应 section 加一行 changelog |
| **改字段名或类型** | 中 (~20%) | 提出者开 GitHub issue 或群里 @ 相关方, C 在 24h 内拍板, 所有人一次性改完后一起测 |
| **改 ES mapping** | 低 (~5%) | B 改前 @ C; 加字段直接加; 改类型需重建 index (demo 数据量小, 几小时内可重跑) |
| **改 agent 调用顺序或职责** | 极低 (~5%) | 架构级改动, 开 30 分钟会, C 亲自改 workflow |

## Changelog

- 2026-05-XX [C] [§1-§8] v0 创建
- (后续改动在这里追加)
