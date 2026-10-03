# Amazon 面试 BQ 回答资源 — ClaimDrift

> 个人面试备考材料。素材全部来自本仓库的真实痕迹:`docs/contracts.md` changelog、`agents/scripts/` 探针与 eval 脚本、`apps/dispatcher/tests/golden/`、git 历史。
>
> 项目规模基线(讲任何故事都可以用这组数字开场):
> **4 周(2026-05-22 → 2026-06-17)、4 人团队、226 个 tracked 源文件 / ~40k LOC、跨 GCP + Elastic 两个云、8 个 ES 索引、6 个 Cloud Run 服务/Job、5 个 ADK agent。我(role C)负责 agents / dispatcher / elastic / BFF 四块,56 个 commit 里提了 31 个。**

---

## 目录

- [Q1. What's your most difficult / technical challenge in a project?](#q1-whats-your-most-difficult--technical-challenge-in-a-project)
- [Q2. Have you used AI tools to improve productivity?](#q2-have-you-used-ai-tools-to-improve-productivity-what-problems-did-you-encounter--how-did-you-use-them)
- [附:通用讲述纪律](#附通用讲述纪律)

---

# Q1. What's your most difficult / technical challenge in a project?

## 选型结论

| 用途 | 故事 | 理由 |
|---|---|---|
| **主线首选** | 候选 1 — Pub/Sub 解耦 | 系统设计深度 + 清晰业务后果;面试官容易往分布式语义追问,且你接得住 |
| **深挖备份** | 候选 2 — 混合检索"假 RRF" | 全项目技术密度最高;当面试官说"再讲一个更技术的"时用 |
| **短平快补刀** | 候选 3 — 120s SSE 缓冲 | 3 分钟讲完,教科书级 Dive Deep,可当"举个 debug 的例子"的标准答案 |
| **"你犯过的最大错误"** | 候选 4 — abstract 假阳性 | COE 四层闭环叙事,极少候选人能讲全 |

---

## 候选 1:5 分钟 cron 触发一条 200 秒的流水线,而平台只给我 60 秒 ⭐ 主线

**难在哪(BQ 的核心,别只讲修复)**

Elastic Workflows 的 `http` connector 在 Serverless 上有**平台写死的 60s 超时,不可覆盖**。而主流程是 supervisor 扇出 5 个 ADK agent,实测 ~200s。这不是"优化一下就能压进去"的问题——它是外部平台的硬边界,只能改拓扑。

**S / T**
每 5 分钟一次的 Elastic scheduled workflow,要触发一条跨 GCP + Elastic 两个云的 5-agent 流水线。

**A**

1. 把 dispatcher 拆成两个端点:
   - `/dispatch` — 只做 bearer 鉴权 + 幂等检查,然后 publish 到 Pub/Sub topic `claimdrift-dispatch`,**~100ms 返回 202**
   - `/run` — 作为 push subscription 的目标,跑真正的 ~200s 流水线(ack-deadline 设 600s)
2. **关键的第二个洞察**:5 分钟的 cron 周期会和 200 秒的流水线赛跑。所以在 `/dispatch` 上加了 `(preprint_doi, published_doi)` 幂等门,重复对返回 `already_processed` 而不是再扇出一次;留了 `?force=true` 逃生口。没有这层,重跑会往 ES 里堆重复 drift_event(调试期确实堆了 5 条)。
3. 踩到的坑:Pub/Sub 的 `--push-auth-token-audience` 必须显式指定,否则它会把 scheme 规范化成 `http://`,`/run` 的 OIDC `aud` 校验就 401。

**R**
主流程稳定自驱;触发链路从"超时失败"变成 100ms 确认 + 异步完成。

**命中 LP**:Dive Deep、Invent and Simplify、Bias for Action

**预备追问**
- 为什么不用轮询 / Cloud Tasks?
- Pub/Sub 是 at-least-once,幂等键够吗?(够——业务主键就是那对 DOI,且落库前先查)
- 消息处理失败怎么办?

**证据位置**:`docs/contracts.md` §9.6.1 + 2026-05-28 changelog;`README.md` 架构图;`apps/dispatcher/README.md` "Runtime sizing"

---

## 候选 2:一个"没有任何报错、结果也返回"的检索 bug ⭐ 深挖备份

**难在哪**

最难的那类 bug:**系统看起来完全正常**。RRF 混合检索查询能跑、能返分、能排序,但两条召回腿其实**都是 ELSER**——因为 `pattern_description` 是 `semantic_text` 字段,根本没有可供 BM25 打分的 text 副本。所以我以为的"稀疏 + 关键词混合",实际是稀疏和自己融合,关键词腿不存在。

**A**

1. **止血** — 触发怀疑的信号是 `_score` 分布不对劲:原设计(§3.5.1)里 `similarity >= 0.75` 的硬阈值在真实索引规模下完全没有区分度。先下线阈值规则,改成让 agent 逐条读 `pattern_description` 自己判相关性;并在契约里标注这个 `_score` **只能作诊断,前端不许拿它排序**。
2. **根因修复** — mapping 里加 `copy_to` 到一个真正的 `text` 镜像字段,**蓝绿重建索引**(ES mapping 不可变,只能重建 + 别名切换),BM25 腿才第一次变成真的。
3. **后续简化** — 实测发现在这个索引规模上,单 `MATCH` 比套 `retriever.rrf` 更简单且不损失表达力,于是把 MCP 工具改回单 `MATCH`,Python 版留作参考规格。

**R**
检索质量修复;并把"分数不可作阈值"写进契约(§3.5.1 / §6.1 都标注),防止团队其他人再踩。

**命中 LP**:Dive Deep、Insist on the Highest Standards、Are Right A Lot

**预备追问**
- 你怎么**验证** BM25 腿现在是真的?(对只在关键词层匹配、语义不匹配的 query 做对照 → `agents/scripts/verify_hybrid_fix.py`)
- 蓝绿期间数据一致性怎么保证?

**证据位置**:`docs/contracts.md` 2026-05-23 changelog + L411 / L618 / L1104;`agents/scripts/probe_*.py`、`verify_hybrid_fix.py`;`elastic/scripts/retrofit_hybrid_lexical.py`

---

## 候选 3:120 秒的"冷启动",其实那一帧在 0.001 秒就发出去了 ⭐ 短平快

**难在哪**

现象:Playground 从 warming 到 ready 要等 120–150s。团队(包括我自己)有四个默认假设——Agent Engine 冷启动、`get_engine` 慢、Vertex 侧慢、CPU/线程池打满。**四个全错**,而且每个都"听起来很合理"。这才是它难的地方。

**A**
我没继续猜,而是在服务端和客户端两侧都给 ready 帧打时间戳做对拍:**服务端 0.001s 发出,客户端 151s 才收到**。这一条数据一次性排除掉全部四个计算侧假设——问题在 SSE 传输层缓冲,不在计算。

**R**
用响应层 keepalive 包装器根治,实测 ready 0.2s 到达。

**命中 LP**:Dive Deep、Customer Obsession

**为什么值得讲**:它展示的是**方法论**——用一次测量把假设空间砍掉,而不是逐个试修复。

---

## 候选 4:一个数据质量 bug 让系统给真实研究者发了假阳性告警 ⭐ "最大错误"专用

**难在哪**

不是代码难,是**已经造成外部后果**:ingestion 在拿不到 published abstract 时回退填了标题占位,drift_analyzer 于是把"标题 vs 摘要"读成剧烈语义漂移 → 假阳性 drift 事件 → 而这个系统的输出是**主动发邮件给下游引用者**。发现时已有 105 条被污染的线上事件。

**A** — 四层闭环,不只是修 bug:

| 层 | 动作 |
|---|---|
| 止血 | `records.py` 不再回退 title |
| 存量 | 下架已存的 105 条事件 |
| 防复发 | dispatcher 写入时自动打标,新数据"出生即隐藏" |
| 兜底 | BFF 的 `visible_query` 层过滤 |

**命中 LP**:Customer Obsession、Ownership、Insist on the Highest Standards

**"what would you do differently"**:应该在 ingestion 层就对"abstract == title"做断言,而不是等下游 agent 产生假阳性才发现。

---

## 候选 5:怎么给一个非确定性的 LLM 系统做可信的 A/B

**难在哪**

要证明 memory loop 不只是"把 patterns 显示在前端",而是真的改变了 agent 的判断。难点是**被测系统本身不确定**:同样输入,agent 的相关性判断会抖。我手动判是 `[]`(不采纳),Playground 自己判却错误采纳了一条 `support_count=94` 的 pattern——直接让实验结论不可信。

**A**
- 设计三臂实验(baseline / treatment / **negative control**),负控是同领域但只有措辞修改的案例,证明 memory 不是被无差别命中。
- 定机器可判的成功判据(`retrieved_patterns_used` 必须含期望 pattern_id、materiality delta ≥ 0.15、拒绝嵌套字段 / 占位 id),写成 `agents/scripts/memory_loop_ab_eval.py`,让实验**可复现**而非靠人眼。
- 定位抖动根因是 agent 的相关性判断而非检索或数据,用 per-run 指令(忽略非 outcome_switch 类型)修复。
- **最有价值的一步**:发现旧的 0.50 baseline 取自 `demo_summary` 而不是真实 readings,是无效对照。**我主动推翻了自己之前汇报过的数字**,重跑后 baseline 是 0.70。

**R**
baseline 0.70 → treatment 0.85,且 rationale 出现质变(agent 显式引用历史复发次数来校准严重度)。

**命中 LP**:Are Right A Lot、Earn Trust、Dive Deep

**预备追问**:样本量够吗?
→ 诚实答:这是 hackathon 规模的定性证据,不是统计显著性;并说明你知道差在哪。

**证据位置**:`docs/memory_loop_ab_test.md`、`docs/memory_loop_v2_design.md`、`agents/evals/`

---

## 候选 6(备用):偶发的 `materiality: null`,真因在配额

4-agent 链偶发断裂 + materiality 偶发 null,看起来像 prompt 或解析 bug。真因是 drift_analyzer 调 gemini-2.5-pro 撞 429 限流返回空输出——**这个信息只在 ReasoningEngine 的运行时日志里,应用层完全看不到**。

**价值点**:当症状出现在 A 层,别假设根因也在 A 层。可作为候选 3 的替代或补充。

---

## Q1 讲述注意事项

| 要点 | 说明 |
|---|---|
| **"I" 不是 "We"** | 明确说"我负责的是 X",不含糊成 we,但也别抹掉队友 |
| **难点要在前 60 秒说清** | 开场就一句:"最难的是平台给了我一个 60 秒硬超时,而我的流水线要 200 秒。" |
| **必带一个"考虑过但否决了"的方案** | 候选 1:压缩流水线到 60s(牺牲 agent 数量)、让 workflow 轮询(cron 精度不够 + 浪费配额)→ 最后选 Pub/Sub |
| **必带"我怎么知道它真的好了"** | 候选 2/3 有硬验证数据;候选 1 用幂等的 `already_processed` 返回值证明 |
| **准备好 "what would you do differently"** | 见候选 4 |

---

# Q2. Have you used AI tools to improve productivity? What problems did you encounter / how did you use them?

> **这题考什么**:不是考你会不会用 AI,是考 **Learn and Be Curious + Ownership**——"你把 AI 当自动补全,还是当一个会自信犯错、需要被架构约束的协作者"。
> **答案重心必须在:你怎么防住它的错**,而不是"它帮我写了很多代码"。

本项目全程用 Claude Code 开发。

## 一、怎么用的(方法论,可迁移)

### 1. 契约驱动 — 让一份文档同时给人和 AI 当 single source of truth

`docs/contracts.md`(1272 行)开头就写明:**prose 给人看,code block 是 source of truth**,直接粘贴到 Agent Builder 工具定义 / ES mapping / 前端 TS 类型。

为什么必须:AI 没有跨会话记忆,每开一个新会话都会**重新猜 schema**。4 人 + AI 并行时,这导致 schema 在三处漂移(mapping JSON / 线上集群 / demo seed)——`drift_events` 的 `retrieved_patterns_used` 在 mapping 文件里就还是 v0 时代的 `nested` 形状。修完之后定了规矩:**mapping 改动必须四件套一起改**(JSON 文件 / 线上集群 / demo seed JSON / changelog),写进契约。

配套是 **changelog 纪律**(每条带日期 + 作者 + 根因)。产生了一个没预料到的作用:**changelog 变成了 AI 的项目记忆**——新会话读一遍就知道踩过哪些坑、哪些方案已被否决,不会再提一遍。

### 2. Spike 优先 — 不让 AI 猜 preview 产品的 API ⭐ 最有价值的一条

Elastic Agent Builder / Workflows 是 **preview 产品**,训练数据里几乎没有。AI 在这种情况下不会说"我不知道",它会**非常流畅、非常自信地编一套 API 出来**。

实际被编到的坑(都在 changelog 里):

| AI 给的 | 实际 |
|---|---|
| http step 输出 `steps.X.output.body` | 是 `.data`,且已自动 parse,不需要 `json_parse` |
| input `type: integer` | 只支持 `string / number / boolean / choice / array` |
| LiquidJS `cgi_escape` 做 URL 编码 | 这个 build 里根本不 work |
| 数组用裸 `{{ }}` 插值 | 会被 stringify,必须用类型保留语法 `"${{ inputs.domain_tags }}"` |

**对策 — 3 阶段 Spike**:写最小可运行探针 → 把真实 API 形状**实测**出来 → 写进 contracts.md → 再让 AI 基于实测结果生成代码。
做 `openalex_citing_works` 时:spike 1 打通 workflow + tool + http + auth;spike 2 摸清 http step 的输出信封;spike 3 验证链式步骤引用 + 真实 DOI。

同一模式用在检索层——改 ES mapping 前先探针验证:`probe_bm25_subfield.py` / `probe_copyto_feasibility.py` / `probe_hotadd_lexical.py` / `probe_rrf_scores.py`。

> **一句话**:AI 的置信度和它的正确率不相关,尤其在新技术栈上。所以我不问它"这个 API 怎么用",我用探针问平台本身,再把答案喂给它。

### 3. Golden + 脚本化验证 — 给 AI 的输出兜一张回归网

- `apps/dispatcher/tests/golden/` 存了 T1 参考运行的真实产物(`t1_drift_event.json` / `t1_affected_citations.json` / `t1_notification_log.json` / 完整 SSE `stream_amblyopia_v2.jsonl`)。AI 改完 dispatcher 之后拿 golden 对拍,不靠肉眼看 diff。
- `agents/scripts/` 下 17 个脚本(`memory_loop_ab_eval.py`、`verify_hybrid_fix.py`、`e4_e2e_check.py`……)本质是同一件事:**把"怎么算改对了"也变成确定性代码**。

## 二、踩到的坑(分数几乎全在这里)

### 坑 1:AI 会沿着你给的框架优化,不会质疑你的框架

最典型的一次:混合检索结果不准。我问"怎么让检索更准",它给的全是**症状层**方案——调阈值、加权重、改 top_k、换 rerank。跟着绕了一圈都没用。

真因是 RRF 两条腿都是 ELSER(见 Q1 候选 2)。

**转折点是我改了提问方式**:不问"怎么优化",而问"把这条 RRF query 里两个 retriever 各自实际打分的字段列出来"。**让它做事实陈述而不是给方案**,根因立刻暴露。

> 教训:AI 极擅长在你划定的解空间里搜索,极不擅长告诉你"你的解空间本身选错了"。**质疑前提是我的活。**

### 坑 2:AI 产出的"格式完全合法"的假数据,会过掉所有校验

- `citation_finder` v0 因为还没接上 OpenAlex 工具,就自己**编了一批格式完全合法、看起来非常真实的 DOI**。这类输出最危险——过得了 schema 校验,也过得了 code review 的肉眼扫描。对策:加 `SYNTHETIC_V0_PLACEHOLDER` 哨兵标记 + 硬性规定"该 agent 输出在接上真实工具前不许落库"。
- `memory_synthesizer` 会幻觉出不存在的 `source_event_ids`。对策:在**写入路径**加一层确定性代码——拒绝幻觉 id、强制重算 `support_count == len(source_event_ids)` 这个不变量。

> **原则:在 LLM 输出和持久化之间,永远留一层确定性的校验代码。**
> **加分讲法**:这条我是先在**用 Claude Code 写代码**时踩到,后来直接写进了**产品本身的架构**。用 AI 造 AI 系统,两边的失败模式是同一套。

### 坑 3:AI 的上下文是仓库,但生产环境 ≠ 仓库

- 排查一个 demo 行为异常排了很久,一直在读本地 `agent.py`。真因是 Agent Engine 跑的是**部署时打包的 prompt**,和本地未提交的版本早就不一致了。AI 看不到线上,它给的每条分析都建立在错误前提上——而且**听起来完全合理**。
- `materiality` 偶发 null:我问"为什么解析出来是 null",AI 就一路在 prompt 和 JSON 解析上找。真因是 429 限流返回空输出,**证据只存在于 ReasoningEngine 运行时日志,不在仓库任何地方**。

> 教训:**AI 只能在我给它的证据范围内推理。扩大证据范围是我的责任,不是它的。**
> 现在凡涉及线上行为的排查,第一步先自己核对部署版本 + 拉运行时日志,再喂给它。

## 三、收益和边界(这段决定你听起来是不是清醒)

**收益**:4 周、4 人、跨两个云、226 个源文件 ~40k LOC。我一个人负责四块(31/56 commits)。这个体量没有 AI 做不完。**它把我的产能从"写代码"挪到了"做架构决策和设计验证"。**

**明确不交给 AI 的三类事**:

1. **架构决策** — Pub/Sub 解耦、inverted topology 是我定的,AI 只负责实现。它优化局部很强,做全局取舍会给你一个"每条都合理但整体走不通"的方案。
2. **有真实外部副作用的操作** — 发邮件、往线上索引 apply。`pattern_curator` 设计成 **dry-run-by-default** 就是这个原因,而且确实出过一次 unbounded apply 事故(事后做守恒验证确认数据没坏)。
3. **实验结论的判读** — A/B 的数字得我自己看。就发现过旧 baseline 取错数据源、是无效对照,主动推翻重跑。

## 四、Q2 讲述结构(约 4 分钟)

| 段落 | 时长 | 内容 |
|---|---|---|
| 定位 | 20s | "我用 Claude Code 做了个 4 周 4 人的跨云项目。但重点不是它帮我写了多少代码,是我怎么设计流程来防住它的错误。" |
| 案例 A | 90s | **Spike 优先** — AI 在 preview 产品上编 API,3 阶段探针 + 契约文档固化 |
| 案例 B | 90s | **混合检索** — AI 只在我给的框架里优化;换成"让它陈述事实"才挖出根因 |
| 边界 | 30s | 三类不交给 AI 的事 + 一次 dry-run 事故 |

**预备追问**

- *"你怎么知道 AI 写的代码是对的?"* → golden 对拍 + probe 探针 + 脚本化 eval,三层
- *"有没有 AI 反而让你更慢的时候?"* → **有,而且两次**。混合检索那次在症状层跟着它绕了一圈;Spike 方法论建立之前,直接信 AI 的 workflow YAML,反复部署失败浪费大半天。**这两次慢正是后来那两条方法论的来源。**(这个回答比"没有"强十倍)
- *"团队里怎么推广?"* → contracts.md + changelog 这套本来就是给 4 个人和 AI 共用的,不是我一个人的私人工作流

---

# 附:通用讲述纪律

1. **前 60 秒必须讲清"难在哪"**,不是"背景是什么"。
2. **"I" 不是 "We"** — 说清自己的边界,但不抹掉队友。
3. **每个故事必须有一个被否决的备选方案** — 证明你做的是取舍,不是唯一路径。
4. **每个故事必须有"我怎么验证它真的好了"** — 数字、golden、对照实验都行,不能只有"跑通了"。
5. **每个故事准备好 "what would you do differently"** — 不要说"没有"。
6. **面试官问"最大错误 / 失败"时,用 COE 四层结构**:止血 / 存量 / 根因 / 防复发。

## 素材索引(需要临场翻证据时)

| 故事 | 仓库位置 |
|---|---|
| Pub/Sub 解耦 | `docs/contracts.md` §9.6.1 + 2026-05-28 changelog;`apps/dispatcher/README.md` |
| 混合检索假 RRF | `docs/contracts.md` 2026-05-23 changelog;`agents/scripts/probe_*.py`、`verify_hybrid_fix.py`;`elastic/scripts/retrofit_hybrid_lexical.py` |
| A/B 实验设计 | `docs/memory_loop_ab_test.md`、`docs/memory_loop_v2_design.md`、`agents/evals/` |
| Spike 方法论 | `docs/contracts.md` 2026-05-24 Phase 5d changelog(3-stage Spike findings) |
| DOI 编造 | `docs/contracts.md` §3.3 NOTE(v0 finding 2026-05-21,resolved 2026-05-24) |
| Golden 回归 | `apps/dispatcher/tests/golden/` |
| Curator dry-run | `docs/pattern_curator_ops.md` |
