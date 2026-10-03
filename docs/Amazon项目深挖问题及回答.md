# Amazon 面试「项目深挖」问题及回答 — ClaimDrift

> 个人面试备考材料。对应 Amazon 上海面试「项目深挖模拟」六个方向。所有素材来自本仓库真实痕迹:`docs/contracts.md`(§9.6.1 拓扑决策 + changelog)、`agents/supervisor_agent/{agent,hardening}.py`、`apps/dispatcher/{main.py,README.md}`、`ingestion/`、`docs/ingestion_cloud_run_ops.md`、`docs/memory_loop_ab_test.md`。
>
> BQ 类问题(最难挑战 / AI 工具使用)见 [Amazon面试BQ回答资源.md](Amazon面试BQ回答资源.md);本文只覆盖技术深挖。
>
> **开场基线数字**:4 周、4 人、5 个 ADK agent(Gemini 2.5 flash×3 + pro×2)+ 1 个纯代码 supervisor、8 个 ES 索引、6 个 Cloud Run 服务/Job、~10k 真实 preprint / ~2.2k 真实 (preprint, published) 对、主流程单次 ~200s。我(role C)负责 agents / dispatcher / elastic / BFF。

---

## 目录

- [讲述纪律(先读)](#讲述纪律先读)
- [方向 1:多 Agent 协作与通信模式](#方向-1多-agent-协作与通信模式)
- [方向 2:Serverless 冷启动与性能优化](#方向-2serverless-冷启动与性能优化)
- [方向 3:语义检索技术选型对比](#方向-3语义检索技术选型对比)
- [方向 4:低置信度处理与人工兜底](#方向-4低置信度处理与人工兜底)
- [方向 5:海量数据抓取与反爬策略](#方向-5海量数据抓取与反爬策略)
- [方向 6:通知可靠性与异常监控](#方向-6通知可靠性与异常监控)
- [方向 7:回顾——如果有更多时间,哪些地方可以做得更好?](#方向-7回顾如果有更多时间哪些地方可以做得更好)
- [附:一页速查表](#附一页速查表)

---

## 讲述纪律(先读)

1. **每个决策都说"为什么"+"备选是什么"+"代价是什么"**。Amazon 面试官追问的模式是 "why not X?",所以每个答案要点里都预埋了被拒绝的备选方案。
2. **没做的就说没做,然后说"如果做我会怎么做"**。本项目是 4 周 hackathon,方向 4(人工兜底)和方向 6(通知监控)有明显缺口。诚实 + 有方案 > 编造。编造在追问第二层就会露馅。
3. **用数字锚定**:60s / 200s / 100ms / 40s→10-20s / 0.75 阈值被废 / 35 条 pattern / 105 条假阳性 / 429 两小时 50 次。数字是"真做过"的信号。
4. **区分"我做的"和"团队做的"**:ingestion(方向 5)是 B 的主责,我做的是接口契约、dispatcher 侧消费和数据质量事故的闭环;讲的时候用"我们"+ 点明自己的贡献边界。
5. **不要把 citation_finder 的 `severity_tier` 当卖点**。它只从标题判断(没有 citation context),本质是占位字段,没有任何下游门控用它。项目里唯一有实际意义的严重度是 drift_analyzer 的 `materiality_score` / `severity_calibration`。被问到就直说"那是弱信号,我们没有拿它做任何决策"。

---

## 方向 1:多 Agent 协作与通信模式

> 图中原题:五个 Gemini 2.5 Agent 如何分工协作?采用何种通信方式(REST API、消息队列)?是同步调用还是异步编排,如何保证一致性?

### 核心答案(2 分钟版)

**分工**按"一个 agent 只做一件事、只读写一个索引"切:

| Agent | 模型 | 职责 | 读 → 写 | 工具 |
|---|---|---|---|---|
| claim_extractor | flash | 摘要/结论 → 句级结构化 claim | preprints → claims | 无 |
| drift_analyzer | **pro** | 两版 claim 集合 diff + 严重度打分 | claims + drift_patterns → drift_events | `search_drift_patterns`(MCP) |
| citation_finder | flash | OpenAlex 找引用它的下游论文 | drift_events → affected_citations | `openalex_citing_works`(MCP) |
| notifier | flash | 每篇受影响论文起草一封邮件 | affected_citations → notification_log | 无 |
| memory_synthesizer | **pro** | 把 drift 事件蒸馏成可复用 pattern | drift_events → drift_patterns | search / create / update(MCP) |

pro 只给两个真正需要推理的(diff + 蒸馏),flash 给三个"结构化转换"的,成本和延迟都省一半以上。

**通信方式——先掀掉问题的前提:agent 之间零通信。**

5 个 agent 是**星形**不是链形。每个子 agent 是一个独立的 reasoning engine,只会"被调用 → 处理 → 返回 → 结束",不知道其他 agent 的存在。所有"把 A 的结果交给 B"都由 supervisor 完成:调 A → 把返回的 dict 存进本地变量 → 筛选/重组成 B 的输入信封(`contracts.md §3.x.1` schema)→ 调 B。所以"REST 还是消息队列"的答案是:

- **supervisor ↔ 子 agent:同步的请求-响应调用**,不是消息队列。具体是 `agent_engines.get(id).async_stream_query(message=json)`,SDK 翻译成对 Vertex AI 的一个 HTTP 请求,响应是事件流。和 REST 同属"我调你、我等你"一类(严格说是 RPC 风格的 HTTP 接口,面试官追问再展开)。选同步的原因:每一步的输入就是上一步的输出,调用方(supervisor)本来就要等,没有解耦的需求。
- **消息队列只出现在入口一跳**:Elastic Scheduled Workflow(每 5 分钟)→ HTTP POST `/dispatch` → **Pub/Sub** → push 到 `/run` → 调 supervisor。这里用队列是因为 Elastic Workflows 的 http connector 有平台写死的 60s 超时,而流水线跑 200s——只有这一处存在"发送方等不起"的矛盾。
- **agent ↔ 工具**:Elastic Agent Builder 内置 **MCP server**(StreamableHTTP)→ ES|QL / Workflow YAML 工具。5 个 agent 共用一个工具面。

星形的好处正是面试官接下来会问的:子 agent 可单独在 Playground 测;顺序/并行/重试/降级全是 supervisor 的 Python 代码,可单测;UUID、时间戳、结果筛选这些数据加工只在一处做。

**同步 vs 异步**:核心链(extractor → analyzer → finder)**同步、fail-fast**,因为每一步的输入就是上一步的输出;两个并行段(extractor ×2 用 `asyncio.gather`,notifier ×N 用 `asyncio.as_completed`)是**同一进程内的并发**;memory_synthesizer 在逻辑上是异步旁路(失败不阻塞主流程),物理上放在 supervisor 扇出末尾 await——这是 v0 的取舍,真正异步需要另一条 Workflow 监听 drift_events,推迟了。

**一致性——先定义面试官担心的四个场景,再逐个给防线 + 真实事故**(系统没有跨服务事务,全靠便宜手段拼):

| 担心的场景 | 防线 | 真实事故 / 已知缺口 |
|---|---|---|
| ① 同一件事做两遍(重复) | `/dispatch` 幂等门查 `(preprint_doi, published_doi)`;`?force=true` 逃生口 | 5 分钟 cron 追 200s 流水线,调试期堆了 5 条重复;查与写之间 200s 窗口非原子 |
| ② 跨表 ID 对不上(引用一致性) | 机器字段(UUID / 时间戳)只有一个生成点且不是 LLM;supervisor 铸 `event_id` 后 yield 权威事件 | T1 Bug 2+4:supervisor 和 dispatcher 各铸一个 UUID,`drift_events._id ≠ affected_citations.drift_event_id` |
| ③ 半成品状态(部分失败) | 父行先单独 `index` 成功再 `bulk` 子行;分阶段降级明确"允许的半成品" | 429 让流静默中止时什么都没写也没失败记录——看不见的半成品 |
| ④ 各 agent 看的"事实"不同(契约 / 数据版本) | `contracts.md §3.x` 唯一真源 + 阶段间 jsonschema 门;`audit_schema_drift.py` 三方比对 | mapping/集群/种子三处漂移;混合检索修复时 alias 误切 shadow 索引 → synthesizer 写主表、analyzer 读 v2(读写分裂),回滚后改原地 retrofit |

### 10 个可能的追问

**Q0. supervisor 是什么,有哪些功能,为什么设计成星形?**(最可能的第一个追问)
- **是什么**:Agent Engine 上的 ADK `BaseAgent` 子类,~460 行 Python,**内部没有 LLM**。对外接口和子 agent 一样(`async_stream_query`),内部跑确定性编排代码,零 token。
- **五个功能**:(1) 定序与并行——extractor ×2 `gather` → analyzer → finder → notifier ×N `as_completed` → synthesizer,顺序写死;(2) 数据搬运与加工——抠出子 agent 的 JSON、存变量、筛选重组成下一个信封(extractor 只取 `claims`;finder 的 N 篇引用拆成 N 个 notifier 信封);(3) 填机器字段——铸 `event_id` 并回流到事件流;(4) 硬化每次调用——90s 超时 / 3 次指数退避 / jsonschema 门;(5) 分阶段降级——核心链 fail-fast、notifier 跳单封、synthesizer 只记日志。
- **为什么星形而不是链形(agent i 自己调 i+1)**:① 控制逻辑必须是可单测的代码,散在 5 个 prompt 里就是"祈祷模型照做";② 子 agent 只认自己的输入 schema,Playground 贴 JSON 就能单独测,上游改格式不牵连下游;③ 数据加工只在一处——双 UUID bug 只改 supervisor 就闭环;④ SDK 限制——ADK 没有远程 engine 的 `sub_agents` 包装器,`ParallelAgent` 子节点构造期固定。
- **代价**:单点;await 全程所以 synthesizer 不是真异步。都记在 changelog。
- 接着被问"为什么不让 LLM 当 supervisor":流程是固定 DAG,没有需要即兴决策的分支,用 LLM 编排是拿不确定性换零收益。

**Q1. supervisor 为什么不是 LLM agent?为什么不用 ADK 的 SequentialAgent / ParallelAgent?**
- supervisor 是 `BaseAgent` 子类、纯代码、零 token。§4.1 的执行顺序是确定的,让 LLM 决定"下一步调谁"只会引入不确定性和成本,还不可单测。
- 不用内置编排类是被 SDK 限制的(google-adk 1.34.0 实测):(1) 没有 `RemoteAgent` 包装器把已部署的 reasoning engine 塞进 `sub_agents`;(2) `ParallelAgent.sub_agents` 在构造期固定,而 notifier 的扇出数 N 要等 citation_finder 返回才知道。所以自己写 `_run_async_impl` yield Event。
- 备选:把 5 个 agent 部署成一个 engine 内的本地子 agent。拒绝原因是每个 agent 要能独立部署/独立在 Playground 测/独立回滚。

**Q2. 为什么中间只有一跳用 Pub/Sub,agent 之间不用消息队列?**
- 只在"两端时间预算不匹配"的地方引入队列:Workflow 60s vs 流水线 200s。agent 之间没有这个矛盾——它们在同一个 supervisor 协程里,直接 await 就够。
- 每多一个队列就多一个"消息格式 + 重试语义 + 死信"要维护。4 周 hackathon 里克制很重要。
- 代价:supervisor 是单点;一个子 agent 卡死会卡整条链。用 hardening 的 per-attempt 90s 超时 + 3 次指数退避兜住。

**Q3. 怎么保证 LLM 输出能被下一个 agent 消费?**
- 三层:prompt 里给完整 §3.x.2 JSON 示例 + "return ONLY JSON";supervisor 端 `_strip_markdown_fence` + 拼接所有 text part 再 `json.loads`(Gemini 会把一个 JSON 拆成多个 part、外面包 ```json);最后 `schemas.py` 的 jsonschema 门。
- 真实教训:第一版 extractor 反向扫 `function_response`,把 MCP 的包装 `{"content":[...],"isError":false}` 当成了 agent 输出。N=0 时恰好不出错(包装里也是空数组),第一个真实 N>0 的 e2e(T1)才暴露——notifier 扇出被整个跳过。所以现在只走 text part 路径。

**Q4. "一致性"具体指什么?怎么保证跨索引 ID 一致?**
- drift_events._id、affected_citations.drift_event_id、notification_log.drift_event_id 必须是同一个 UUID,否则前端 join 不上。
- T1 Bug 2 + Bug 4:drift_analyzer 按契约返回 `event_id: null`(LLM 不能可靠生成 v4 UUID),supervisor 铸 UUID 给下游信封,但没把铸完的 event 回流到 stream;dispatcher 解析的是 analyzer 原文(null),自己又铸了一个 → 两个 UUID。修复:supervisor 在 analyzer 之后 yield 一个 `author=supervisor_agent` 的权威事件,dispatcher 以它为准。
- 通用原则(写进 changelog 的两条):**任何 `*_at` 时间戳和 id 都归 orchestrator 填,不归 LLM**;LLM 填的时间会锚到训练日期。

**Q5. 幂等怎么做?为什么需要?**
- 5 分钟 cron 和 200s 流水线赛跑:watermark 还没推进,下一个 tick 可能再发同一对。`/dispatch` 先查 drift_events 里有没有同 `(preprint_doi, published_doi)`,有则返回 200 `already_processed`,留 `?force=true` 逃生口。
- 调试期确实堆了 5 条重复 drift_event 才加的。
- 局限:检查和写入不是原子的(先查后跑,200s 后才写),两次 tick 落在同一个窗口仍可能重复。真要严就在 dispatch_state 里加 `in_flight` 集合或者用 ES 的 `op_type=create` 抢占一个 lock 文档。

**Q6. 失败处理策略是统一的还是分阶段的?**
- 分阶段,写在 supervisor 里:核心链 fail-fast(缺任一 claim 集合 analyzer 就没法跑);notifier 每封邮件独立 try/except,一个收件人失败只 yield 一个 `error_code=sub_agent_failed` 事件,其他继续;memory_synthesizer 失败只记日志,因为 drift_event + 邮件已经落地,少一次记忆写入可以由 curator 或下一个事件补。
- 重试时缓冲每次尝试的事件,只把成功那次的事件 yield 出去——否则失败尝试的半截事件会泄漏进 SSE,前端看到"幻影 agent"。

**Q7. 见过什么"整条链静默断掉"的故障?根因?**
- drift_analyzer 调 gemini-2.5-pro 撞 429 RESOURCE_EXHAUSTED(两小时 50 次),ADK 抛 `_ResourceExhaustedError`,agent 产出 1 个空 chunk;schema 门判失败 → `RuntimeError` → **Agent Engine 静默终止流、不把异常传给客户端** → 后面 3 个 agent 全不亮。
- 表现是"偶发":空闲时能跑,配额耗尽时稳定失败。
- 教训:agent 空输出先查 reasoning engine 运行时日志(`resource.type=aiplatform.googleapis.com/ReasoningEngine`)的 429,不要先改 prompt。
- 修法(部分待做):申请配额 / 降级 flash / supervisor 对 429 单独退避 / Provisioned Throughput。

**Q8. 为什么编排放在 GCP(ADK supervisor)而不是 Elastic Workflows YAML?**
- 研究结论:Elastic Workflows 的 `ai.agent` step 只能调 Agent Builder 内注册的 agent,不认识 Vertex 资源;用通用 http step 调 `streamQuery` 能通,但 Workflows 没有 secret store,GCP access token 1 小时过期,要在 YAML 里写"取 token → Liquid 插值到下一步 header"的刷新舞步——脆弱、难测、把 agentic 逻辑藏进模板。
- 于是反转拓扑:Elastic 做它擅长的(调度 + 存储 + MCP 工具面),GCP 做编排。三个轴上都更好:规则契合(两家产品都"合理使用")、demo 可录(Playground 能看到扇出 trace)、实现风险(Cloud Run SA 鉴权是 GCP 惯例)。

**Q9. Agent 之间传的上下文有多大?怎么控制?**
- 信封只传必要字段:analyzer 拿两个 claim 数组;finder 拿 `drift_summary + claim_diffs`(不拿全文);notifier 拿单篇引用 + diffs;synthesizer 拿完整 drift_event + 引用**汇总计数**(不是 47 篇列表)。
- 每个 agent 是独立会话(`VertexAiSessionService`),不共享历史,所以没有上下文累积问题。

**Q10. 如果引用数 N 很大(几百),notifier 扇出会怎样?**
- 现在 `per_page=25` 封顶在 citation_finder 端,所以 N≤25。
- 真要放开:as_completed 已经是并发的,瓶颈会变成 Gemini QPS 配额和 Gmail 发信配额,需要加 `asyncio.Semaphore` 限并发 + 把 notifier 扇出改成独立的 Pub/Sub 扇出(每封邮件一条消息),让 Cloud Run 横向扩。

---

## 方向 2:Serverless 冷启动与性能优化

> 图中原题:Cloud Run 冷启动延迟大概在什么范围?对系统整体响应速度影响多大?是否采用了预热或常驻实例策略?

### 核心答案(2 分钟版)

**冷启动范围**:dispatcher 是 Python + FastAPI + vertexai SDK,容器起来后第一次 `agent_engines.get()` 要解析 reasoning engine,实测冷启 **~40s**;加 `--cpu-boost`(启动前 5s CPU 翻倍)后 **10-20s**。纯 Python 容器本身 1-3s,大头是 SDK import + 首次 Vertex 调用。

**对系统的影响要分端点看**:
- `/dispatch` 必须在 Elastic Workflow 的 60s 内返回。40s 冷启 + 幂等查询 + 发布 Pub/Sub,风险太高 → **`--min-instances=1` 常驻一个实例**。这是唯一必须做的预热,不是优化项。
- `/run` 跑 200s 流水线,里面 LLM 推理占 90%+,10-20s 冷启是噪声,不值得为它常驻 20 个实例。
- 前端 BFF 读 ES,单次 <100ms,冷启只影响第一个用户。

**运行时尺寸的四个 flag 是一套**,不是各自调的:`--concurrency=1`(一个实例一条流水线,不抢事件循环)+ `--max-instances=20`(一个 tick 最多 20 对,`size:20`)+ `--cpu-boost` + `--timeout=600`(和 Pub/Sub ack-deadline=600 对齐)。

**最值得讲的性能故事**是一个"看起来像冷启动但不是"的 120s 卡顿——见 Q4。

### 10 个可能的追问

**Q1. 为什么 `/run` 要 await 整条流水线再返回,而不是立即 202 + 后台任务?**
- 早期版本就是 202 + `asyncio.create_task`。结果 Cloud Run 自动扩缩只数 **in-flight HTTP 请求**,100ms 就返回的 handler 让每个实例都"看起来空闲",服务永远只有 1 个实例,单事件循环被后台任务压垮。
- 改成 await 后,每个实例可见地忙,Pub/Sub 并发 push 才能把服务扩到 max-instances。
- 代价:一次请求占 200s,所以 ack-deadline 和 --timeout 都要 600s。

**Q2. min-instances=1 的成本?值吗?**
- 一个 1 vCPU / 常驻实例,月成本个位数到十几美元量级。它消除的是**组合风险**:不常驻时每小时 ingestion 后的第一次 POST 大概率冷启动(Cloud Run 空闲缩零),40s 冷启(无 boost)吃掉大部分 60s 预算,再叠加一次 ES 抖动(如 delete_by_query 堆积把幂等查询拖到 >20s)就超时。
- 超时的后果要说准:http step 无 `on-failure`,失败即中止 workflow,第 4 步水位**不推进**,下一轮重发同一批——是"延迟 5 分钟 + 可能重复",**不是丢失**。真正会丢的是 `size:20 + sort desc` 水位跳跃(3067 对只发了 39),与冷启动无关。
- 更便宜的替代是 Cloud Scheduler 每分钟 ping `/health`,但那是"概率保温",min-instances 是确定的。

**Q3. Agent Engine 侧有冷启动吗?**
- 有,但是托管的、我们不可控。观察到子 agent 首次调用比后续慢几秒到十几秒;真正的时间大头是 gemini-2.5-pro 推理(analyzer 几十秒)。所以没有为 Agent Engine 做预热,把精力放在减少串行段:extractor ×2 并行、notifier ×N 并行。

**Q4. 讲一个你排查过的性能问题。**(⭐ 标准 Dive Deep 答案)
- 现象:Playground 编排演示里 `pipeline.warming → pipeline.ready` 卡 ~120s(抖动 17-282s)。
- 错误归因过一整轮:`import vertexai` 慢(顶层 import 后还是 135s)、`agent_engines.get()` 慢(埋点 0.0s)、CPU 节流(`--no-cpu-throttling` 无效,回退)、线程池排队(0.0s)、缓存不跨请求(单 worker pid=1 证明有效)。
- 转折:**同时**打服务端 `print(flush=True)` 发出时刻和客户端到达时刻。服务端从 enter 到 yield ready 在**同一毫秒**;客户端前 3 帧 0.2s 到,ready 帧 151s 才到 → 151s 全在传输层。
- 根因:ready 帧孤立地跟在一个 `await asyncio.to_thread(...)` 后面,前 3 帧是连续 burst 触发了 flush,ready 帧落进 GFE/HTTP2 缓冲,直到后续真实数据把缓冲顶破。所以延迟不固定。
- 修复:在 StreamingResponse 响应层包一个 `_with_keepalive(gen, 0.5)`,生成器静默超 0.5s 就 yield `: ka\n\n` 注释帧(EventSource 规范忽略)。ready 从 17-151s 降到 **0.2s**。
- 两个坑:只包 warming 段无效(热实例下那段本来就是 0s,真正的静默在 ready → 第一个 LLM chunk 之间);`Cache-Control: no-transform` GFE 不尊重。
- 教训:诊断流式时序必须两端对照;`log.info` 在那个服务里根本不可见(root level=WARNING),要用 print。

**Q5. 有没有因为"性能优化"反而搞出问题的?**
- 有。`/run` 每次都 fire-and-forget 一个 `agent_events` 保留期清理(`delete_by_query`)。批量回填 ~3000 对/10h 时,清理任务在 ES 端堆积,每个都全表扫,开始超时(>54s),1 vCPU 被吃光,连带 `/dispatch` 的幂等查询变慢,回填脚本看到 25% 客户端超时。
- 修:节流到每 N 次 `/run` 跑一次(`_next_sweep_counter`)。concurrency=1 下某个实例跳过的 sweep 会被别的实例捡起来,所以不会漏。

**Q6. 为什么 Python 而不是 Go/Node 减小冷启?**
- ADK 是 Python-first;vertexai SDK、elasticsearch async 客户端都是 Python 成熟度最高。冷启动的大头是 SDK 初始化不是解释器,换语言收益有限,而且失去 ADK。
- 如果冷启真成瓶颈,先做的是懒加载(`get_supervisor()` / `get_gmail_service()` 都已经是懒的)、减 requirements、用 min-instances,最后才考虑换语言。

**Q7. 流水线 200s 里时间花在哪?怎么再压?**
- 粗分:extractor ×2 并行 ~20s;analyzer(pro + MCP 检索)~60-90s;finder(OpenAlex 两次 HTTP + flash)~20s;notifier ×N 并行 ~20-30s;synthesizer(pro + 3 次 MCP)~40s。
- 压法按性价比:synthesizer 真异步化(省 40s 主流程);analyzer 用 Provisioned Throughput 或降 flash(质量代价);流式输出让前端先看到部分结果(已做,SSE)。

**Q8. 多实例并发时 ES 会不会成瓶颈?**
- 20 实例 × 每条流水线几十次 ES 读写,对 Serverless ES 来说很小。真实瓶颈出现过一次就是 Q5 的 delete_by_query,不是正常读写。
- 写用 `async_bulk`,单文档写只用于必须先成功的父行。

**Q9. Ingestion 侧的 Cloud Run Job 有冷启动问题吗?**
- 没有意义上的问题:Job 每小时跑一次,冷启 10s 对比一次拉取几分钟是噪声。三个 Job 错峰(0/10/30 分)是为了避免同时打 ES 和外部 API,不是为了冷启。

**Q10. 前端首屏怎么优化的?**
- Next.js 在 Cloud Run,BFF URL 编译期内联(`NEXT_PUBLIC_BFF_URL`);统计接口用 ES 聚合一次返回而不是拉全量;SSE 用 `Last-Event-ID` 断线续传,不重放整条流。

---

## 方向 3:语义检索技术选型对比

> 图中原题:为何选择 ELSER 语义检索而非传统向量数据库?在召回率、精准度上的实际效果差异有多大?针对学术文本有何优化?

### 核心答案(2 分钟版)

**用在哪**:检索的对象不是论文,是 **drift_patterns**——memory loop 的记忆库。drift_analyzer 分析新 drift 时用结构化描述子(领域 + 漂移类型 + 幅度)去检索历史 pattern,拿到 `support_count` 做基率校准;memory_synthesizer 写入前检索判断"是同一现象吗"决定 create 还是 update。

**为什么 ELSER 而不是外部向量库**(按重要性):
1. **数据、记忆、检索在同一个存储里**。8 个索引全在 ES,agent 通过 MCP 用 ES|QL 查。加一个向量库意味着两套系统要同步、两套鉴权、两套 schema 漂移要审计。我们光 ES 一套就出过三处 schema 漂移(mapping JSON / 集群 / 种子数据),专门写了 `audit_schema_drift.py`。
2. **ELSER 是 learned sparse,零样本、领域外鲁棒**。学术文本(药名、基因符号、统计术语)dense embedding 需要微调或者域内模型;ELSER 的稀疏展开保留了词项本身,对术语友好。而且 `semantic_text` 字段自动做分块和推理,没有 embedding 流水线要维护。
3. **可以和 BM25 做混合(RRF)**——学术文本的专有名词/缩写正是 BM25 的强项。
4. 诚实的第四点:hackathon 是 Elastic track。但前三点独立成立。

**召回率 / 精准度——诚实版:当前规模测不出差别,给理论分析 + 验证方案**:
- 参数(代码核对):N = 35 条 pattern;analyzer `top_k=10`(prompt 指定,`agent.py`)、synthesizer `top_k=5`;RRF `rank_constant=60`、`rank_window_size=max(4k,20)=40`;每查询相关文档 R ∈ {0,1}(synthesizer + curator 保证一现象一条)。
- 线上路径:两个 agent 都走 Agent Builder 的 ES|QL 工具 `search_drift_patterns` = **ELSER-only**(ES|QL 不支持 RRF);BM25+ELSER 的 RRF 混合是 Python 参考实现(`_shared/elastic_retrieval.py`),用在 pattern_curator 去重和 E4 / verify_hybrid_fix 等评测脚本里。
- **N=35 时三种检索器 Recall@10 都 = 1.0,这是结构决定的**:window 40 > N,两条腿都把整个索引送进融合,RRF 不可能漏;随机排序都有 10/35 = 29%;E4 实测目标 rank 1。不是三者等价,是语料太小分不开。**不要报任何"实测 95% / 75%"之类的数字——没有标注集,没测过。**
- **精准度被结构封死 ≤ 10%**:R ≤ 1、k = 10 → Precision@10 ≤ 0.1,新现象时 = 0。这是故意的(prompt:"larger candidate set is intentional… you must inspect candidates yourself"):检索层做召回,LLM 做精准。有意义的指标是 **post-LLM 采纳精准度**(`retrieved_patterns_used` 里真相关的比例),它掉过一次(support=94 不相关 pattern 被采纳)。
- **RRF 混合不天然比单腿召回高——用真实参数能证明**。RRF = Reciprocal Rank Fusion,score(d) = Σ 1/(k + rank),k=60。相关文档只在 ELSER 腿 rank 1、BM25 腿零重叠 → 1/61 = 0.0164;一个干扰文档两腿都在 window 内哪怕都排第 40 → 2/100 = 0.0200。**只要 window ≤ 62,任何"两腿都有"的文档一定压过"只在一腿、哪怕第一"的文档**——当前配置是共识优先(intersection-first),不是并集。推论:相关文档两腿都进 window 时混合稳赢;掉出一腿 window(改写导致 BM25 零重叠)时会被所有共识干扰压下去,N 大后两腿 top-40 交集轻易 > 10,这条 query 直接归零,即便 ELSER 单独把它排第一。混合是否更高取决于两腿失效模式是否独立——学术文本恰好独立(BM25 死于同义改写,ELSER 死于药名/基因符号被 wordpiece 切碎),所以经验上平均赢,但不是每个 query 都赢。扩库时 `rank_window_size` 必须提到 ≥ 100(2/(60+w) < 1/61 需要 w > 62),这个 `max(4k,20)` 是给 N=35 写的。
- **扩库后估算**(标明是估算;query = 结构化描述子,设 60% 普通改写 / 40% 含罕见 token):

  | 规模 | BM25 | ELSER | RRF 混合(window ≥ 100) | RRF 混合(现 window=40) |
  |---|---|---|---|---|
  | N=35(现状) | 1.0 | 1.0 | 1.0 | 1.0(E4 实测 rank 1) |
  | N≈500 | 0.80–0.90 | 0.95–0.98 | 0.97–0.99 | ≈ ELSER,可能略低 |
  | N≈5000 | 0.55–0.70 | 0.85–0.90 | 0.90–0.95 | 0.80–0.90,可能低于纯 ELSER |

  依据:BM25 掉得最快(pattern_description 是 LLM 改写文本,同领域 pattern 一多就得靠漂移类型词汇区分,改写命中不了;罕见 token query 反而是它强项);ELSER 掉得慢但有天花板(罕见 token 展开稀释、语义展开让"同领域不同漂移类型"看起来相近);混合增益集中在罕见 token 子集,上界 1 − p_B·p_E ≈ 1 − 0.4×0.12 ≈ 0.95。直觉公式:每条干扰独立超过相关文档的概率 q,期望名次 ≈ 1 + N·q,Recall@10 在 N ≈ 9/q 处开始塌,q 差一个数量级膝点差 10 倍规模——所以三条曲线 N=35 重合、N=500 后才分开。
- **已测**:E4 注入 2 条域外 pattern(经济、神经),结构化描述子查询 hybrid / ELSER-only 均 rank 1;修假 RRF 前 hybrid−ELSER 差异恰好为 0(两腿字节相同)。
- **补实验设计**(`e5_retrieval_ir_eval.py`,复用 E4 `_rank_of` + inject/cleanup 工具;ES 已下线,可用本地 Docker ES 试用许可或 SPLADE 代理 ELSER 离线跑):标注集 = 35 pattern × 3 改写描述子(105)+ COMPARE 51 例 outcome_switch 描述子 + AB 3 例 + E4 2 例 + 20 条 ∅ 查询,单独标记稀有 token 子集;三检索器(BM25 镜像字段 / ELSER MATCH / 真 RRF)× k∈{1,3,5,10} × window∈{40,100,200};指标 Recall@k、MRR、post-LLM Precision、∅ 查询误采纳率;注入 500/2000/5000 干扰 pattern 画退化曲线。判读:稀有 token 子集 hybrid 应高 10-20 点,纯改写持平;若 window=40 时 hybrid < ELSER 即验证上面的共识优先推导;误采纳率 >10% 则在 ES|QL 加 `pattern_type`/`domain_tags` 预过滤。
- **真正学到的是 `_score` 不能当阈值用**——见 Q2。

### 10 个可能的追问

**Q1. 讲讲那个"假混合检索"bug。**(⭐ 全项目技术密度最高)
- 设计是 RRF 融合 BM25 + ELSER。实现里 BM25 腿写的是 `match: {pattern_description: ...}`,但 `pattern_description` 是 `semantic_text` 类型,`match` 打到它上面会**自动路由到 ELSER 推理**。两条腿是字节级相同的 ELSER 查询——RRF 在和自己融合。
- 发现方式:`probe_rrf_scores.py` 打印两腿原始 `_score`,完全一致。
- 修复零写入代码改动:给 `semantic_text` 字段加 `copy_to: pattern_description_text`(普通 `text` 字段),BM25 腿指向镜像字段;线上索引用 `PUT _mapping` + `_update_by_query` 原地回填 35/35,不需要蓝绿重建。
- 中间还走错一步:第一次用 alias 切到 shadow 索引 `drift_patterns_v2` 部署,导致写(synthesizer → 主索引)读(alias → v2)分裂,回滚后改原地。教训:shadow/alias 机制只给人工触发的 taxonomy 回填用,不是稳态。

**Q2. 相似度阈值怎么定的?**
- v0 契约写的是 `similarity_score >= 0.75`。实测两次推翻:RRF 分数是基于排名的,不携带相关性;ELSER `_score` 在 35 条的小索引上分布不稳,同一 query 相关/不相关的 score 能重叠。
- 于是**废掉阈值,把相关性判断交给 LLM**:检索只负责 top-k(analyzer k=10,synthesizer k=5;`search_drift_patterns` 函数默认值才是 3),agent 读每条 `pattern_description` 判断"是不是同一现象",用了的回填到 `retrieved_patterns_used`。
- 代价:相关性判断会抖。Playground baseline 污染事件就是 analyzer 把不相关的 pattern 判成相关、采纳了 support=94 的基率。修法是在 per-run 指令里加"忽略非 outcome_switch 类型"——本质上是把一个硬过滤加回 LLM 判断前面。

**Q3. 为什么不用 Vertex AI Vector Search / pgvector?它们也是 Google 生态。**
- Vertex Vector Search 要单独建 index endpoint、单独的写入路径、单独的 ACL,而且不带全文/BM25;pgvector 意味着再加一个 Cloud SQL。
- 我们的检索对象只有几十到几百条 pattern,ES 一个字段就够;把复杂度花在 memory loop 的读写语义上(create-vs-update、support_count 不变量、curator 治理)比花在存储上收益大得多。

**Q4. 学术文本上具体做了什么优化?**
- **查询侧**:不用原始 claim 文本查,用结构化 drift 描述子(domain + drift_type + magnitude)。E4 验证它在训练集外泛化。原因:原始 claim 全是实验细节噪声,pattern 描述的是"现象"。
- **写入侧**:`pattern_description` 写作规范——30-80 词、必须含领域 + 漂移类型 + 幅度、**不含 DOI / 作者名 / 具体药名**(那些走 `source_event_ids`)。这是为检索质量设计的,不是为可读性。
- **混合检索**:BM25 腿专门为专有名词和缩写(HCQ、NAFLD、基因符号)兜底。
- **分块**:`semantic_text` 自动按 ELSER 的 512 token 窗口分块,pattern 描述短,一块就够。
- **Serverless 坑**:不能挂 ingest pipeline 把 ELSER 输出写回同一个 `semantic_text` 字段——它要求原字段保持标量文本。

**Q5. 召回率、精准度到底怎么衡量的?**
- 直说:没有传统 IR 指标。理由是语料太小、没有标注集。
- 替代评估三层:E4 泛化探针(rank 1 命中);memory loop A/B(seed → treatment 检索到、negative control 不检索到 → 证明"读侧"真的在用);E2 累积曲线([5,20,35,50] 四档全真实 COMPARE 病例,看 `calibrated_materiality` 随 support_count 单调上升)。
- 如果要做正经 benchmark:用 COMPARE 的 51 个 outcome-switch 病例做标注集,ELSER-only / BM25-only / hybrid 三组算 Recall@10 / MRR;但 N=35 时三组都会是 1.0,必须先注入干扰 pattern 扩库到 500+ 才有区分度(见方向 3 补实验设计)。

**Q6. ELSER 的局限?**
- 英文 only(项目全英文语料,没踩到);稀疏展开的 token 上限 512;推理在 ES 端,写入吞吐比纯 BM25 低(我们写入极少);`_score` 不可比(Q2)。
- 最实际的局限:**调试不透明**。dense 可以看向量距离,ELSER 的稀疏权重要专门 `_inference` 才能看。

**Q7. RRF 的 k 参数、两腿权重怎么定?**
- 用默认 `rank_constant=60`,没调。原因同 Q5:语料太小调了也不显著。用 RRF 而不是加权线性融合是因为两腿分数量纲不同(BM25 无界、ELSER 稀疏点积),RRF 只看排名不看分数。

**Q8. 检索结果怎么进 prompt 的?会不会污染?**
- 通过 MCP 工具返回,不是 orchestrator 预注入——这是 §3.2.1 的明确改动。原因:让 agent 自己决定查不查、用哪条,Playground trace 里能看到 tool_call,可审计。
- 污染确实发生过(Q2),对策是 prompt 里的类型过滤 + `severity_calibration.rationale` 强制写出用了哪条基率,人能复核。

**Q9. 为什么 pattern 治理要单独一个 curator 而不是让 synthesizer 顺便做?**
- 写路径要低延迟、单一职责(只 append evidence)。合并重复、刷新描述、淘汰低质是批处理,放一起会让主流程变慢且不可控。
- curator 是 dry-run-by-default 的 Cloud Run Job;有过一次 unbounded-apply 事故,数据没坏(守恒校验通过),但让我们把"默认不写"变成了硬规则。

**Q10. 如果 pattern 涨到 10 万条,方案还成立吗?**
- 存储成立(ES 本来就是为这个规模设计的);变的是:top-k 要加 domain 预过滤(`domain_tags` 是 keyword,ES|QL 的 WHERE 就能做);curator 要从全量扫改成增量;LLM 判相关性的 k 不能再大,所以检索质量的权重上升,那时才值得做 Q5 的正经 benchmark 和 RRF 调参。

---

## 方向 4:低置信度处理与人工兜底

> 图中原题:当 Agent 判断置信度低于阈值时,系统会触发什么降级流程?是否有人工审核兜底机制?如何确保输出结果的可靠性?

### 核心答案(2 分钟版)——先诚实定边界

**直接回答**:v1 **没有**"置信度低于阈值 → 自动降级 / 进人工审核队列"的显式机制。项目在 4 周内把力气花在了"让判断可审计"和"让错误不扩散"上,而不是"让人参与"。具体是这样保证可靠性的:

1. **判断本身带解释**:drift_analyzer 输出 `materiality_score`(0-1)之外,还必须输出 `severity_calibration`:无记忆基线分、校准后分、delta、用了哪条 pattern、`support_count`、一句 rationale。人看一眼就知道分数怎么来的、记忆是抬高还是压低了它。
2. **确定性门在 LLM 后面**:每个 agent 输出过 jsonschema 门;不通过就重试,3 次不过就按阶段降级(核心链断、notifier 跳单封、synthesizer 只记日志)。这是代码,不是 LLM。
3. **通知本身是"人工兜底"的设计**:邮件里明确写"这是自动检测通知,是否需要更新由作者判断"——系统不下结论,把判断权交给最有资格的人。demo 阶段所有邮件发到团队测试邮箱(`DEMO_FALLBACK_EMAIL`),等于有人在看每一封。
4. **数据质量事故的四层闭环**(⭐ 最有说服力的可靠性故事):published 行 abstract 缺失时回退成标题,analyzer 把所有 claim 判成"消失",材料性虚高 → 105 条假阳性。闭环:止血(不再回退)+ 下架已存 105 条 + dispatcher 写入时自动打标(新数据出生即隐藏)+ BFF `visible_query` 过滤。

**如果再做一版**,我会加三样:发信前的 `materiality_score` 门(<0.3 只记录不发);`central` 级引用进人工复核队列再发;邮件里加"这个判断对/不对"反馈链接回写 ES,做闭环标注集。

### 10 个可能的追问

**Q0. "drift 严重程度"怎么定义的?为什么历史 pattern 库能作为评价依据?**
- **定义**:`materiality_score` ∈ [0,1],回答"下游引用者需要多大程度重新审视自己的工作"。由 drift_analyzer(pro)两步给出:① 看 diff 本身——7 种 diff_type + 四档指导(0-0.3 措辞 / 0.3-0.6 数值 <50% 或加限定 / 0.6-0.9 数值 >50% 或结论消失 / 0.9-1 反转、显著性丢失)→ `baseline_materiality_without_memory`;② 检索 `drift_patterns`,按命中 pattern 的 `support_count` 上调/下调 → `calibrated_materiality` = 最终分。两个分、delta、pattern id、rationale 全在 `severity_calibration` 块里。
- **为什么 pattern 库能当依据**:单个 diff 有歧义,pattern 提供**基率**。主案例:主要终点被降成探索性终点,只看 diff 是 0.6-0.7("指标还在,只是位置变了");pattern 库里"临床试验/主要终点降级/支持 50 例"告诉你这种改动历史上几乎总意味着疗效主张没过同行评审 → 0.85+。反向也成立(pattern 说是常规单位换算 → 压低)。本质是把有经验审稿人的"上次见到这种事最后是什么性质"沉淀进索引。
- **怎么保证不是拍脑袋**:先独立给基线再校准,两个数都输出;`support_count` 连续量、边际递减,E2 用 51 个真实 COMPARE 病例按 [5,20,35,50] 验证单调+渐近;A/B 同一病例 0.75 → 0.85 且 rationale 质变;pattern 库有 curator 治理、默认 dry-run。
- **第二个动机——稳定性**:单案例裸判对 **system prompt(agent INSTRUCTION)措辞**极敏感,不是对输入敏感。实例:同一 tasimelteon case 输入不变,删掉 INSTRUCTION 里一句"this case is … medium"的锚定提示,基线从 0.50 漂到 0.70。pattern 库提供的是不随指令措辞变化的外部锚点,相似事件检索到同一 pattern 后分数趋于一致。顺带的诚实点:那句提示原本在人为压低基线放大记忆效果(0.50→0.82 很戏剧),删掉后基线 0.70 更真实但曲线变平——我们选了诚实版。
- **边界**:分数没和人工标注对齐,只用于排序展示不挂自动动作;相关性判断被污染过一次(support=94 的不相关 pattern 被采纳),靠 prompt 类型过滤兜着。
- **别混淆**:citation_finder 的 `severity_tier` 只从引用论文标题猜,无决策依赖;有实际意义的严重度只有 drift_analyzer 这一个。

**Q0b. claim drift 的实际影响是什么?为什么严重度打分是痛点?(带文献数字)**
- **纠正前提**:drift 是评审的产物,发表版是对的;受害者是第三方——在 preprint 阶段就引用并往下建的人(后续论文、博士论文、基金、指南、媒体),没人通知他们。
- **多常见**:PLOS Biology 2022 追踪 bioRxiv/medRxiv:非 COVID 93% 结论不变,但 **7.2% 非 COVID / 17.2% COVID 发表时结论重大改变** → 大多数良性、少数致命 → 需要打分分诊而不是二值检测。
- **多少人受影响**:四大医学期刊 2020 COVID 论文 **29.3% 引用 medRxiv preprint**;被引 preprint 中 **58.9% 在引用文章上线后才发表**,近半在标题/数据/结论上与发表版不同;medRxiv 只标注 39.7% 的发表链接 → citation_finder + notifier 的存在理由。
- **最坏情况**:羟氯喹 preprint 被 Fox News 引用后 10 天撤回;"数百万人不必要服用",心脏事件、药物短缺 → 伤害发生在 preprint 到修正的时间窗里。
- **为什么 outcome_switch 是旗舰 pattern**:COMPare 监测显示试验平均只报 62% 预注册结局、静默新增 5.3 个,主要终点 76.3% 如实报告 → 稳定基率的系统性现象,值得沉淀;E2 直接用 COMPare 51 个真实病例。
- **一句话**:检测不稀缺,分诊稀缺——在 93% 良性里挑出 7% 会让下游失效的,而同一 diff 表面在不同领域含义不同,只有历史 pattern 库能给这个上下文。
- **`support_count` 高 ≠ 更严重**:它是可信度,方向由 pattern 本身决定(`calibration_effect: raised/lowered`);"单位换算"pattern 支持数越高越该下调。

**Q0c. 库很小的初期,"这类现象意味着什么"怎么判?LLM 怎么知道"主要终点降级 = 没过审"?**
- **含义不是从库里学的,是三个来源叠加;库积累的是"含义适用"的证据强度。**
  1. 模型预训练知识——outcome switching 是临床方法学常识(CONSORT / COMPare),所以无记忆基线已是 0.6-0.7 而非 0.2,只是不够确信。
  2. analyzer system prompt 写了示例([agent.py:156-165](../agents/drift_analyzer/agent.py#L156-L165)):"a single primary-outcome switch may look only significant … memory showing this recurs and often means the headline efficacy claim no longer survived publication → raise"。**这句因果是我们从 COMPare 借来写进去的人为先验**,面试要主动承认。
  3. `pattern_description` 自带含义——synthesizer(pro)在 `support_count=1` 创建时按规范(领域+类型+幅度,30-80 词,无 DOI)写出,如 "…demote primary efficacy endpoints to exploratory…, reflecting a reassessment of therapeutic benefit";之后 update 只追加 event_id、计数,描述不变。
- **初期时间线**:空库 → 只靠 1+2 给基线,`severity_calibration: null`;首条 pattern(support=1)→ 含义有了但 prompt 规定 "with little support … stay near the memory-free baseline",几乎不动;5/20/50 → 边际递减上调(E2);demo 冷启动用 `demo_seed/drift_patterns.json` 一条 support=4 的种子。
- **主动说的弱点**:① prompt 示例专写 outcome_switch,E2/demo 也是 outcome_switch,有"对着考题写提示"之嫌,对其他类型只有"cosmetic → 压低"的泛化规则;② 描述里的含义 LLM 一次写成、从不修正(update append-only,curator 只合并不校验因果),写错了会被 50 个事件越养越"可信";③ 真正学到含义需要收件人反馈回写,没有它"越用越准"准的是计数不是解释。

**Q1. `materiality_score` 的阈值怎么定的?有验证吗?**
- 契约给了四档指导(0-0.3 minor / 0.3-0.6 medium / 0.6-0.9 significant / 0.9-1 major),前端按 ≥0.7 高、≥0.4 中展示。
- 验证靠 E2 累积曲线:同一 outcome-switch 病例,pattern 的 support_count 从 5 → 50,`calibrated_materiality` 单调上升且边际递减——说明分数对证据强度敏感,不是随机。
- 没做的:没有人工标注的"真实严重度"去对齐绝对值。所以阈值是相对的、用于排序和展示,不用于自动行动。这正是没做自动降级的原因——**没有校准过的分数上不该挂自动决策**。

**Q2. 那 citation_finder 的 `severity_tier`(central/comparative/peripheral)呢?**
- 直说:它只能从**引用论文标题**和 drift_summary 判(§1.3 决定不抓引用论文 PDF,OpenAlex 也不给 citation context),信号很弱。它被传给 notifier 和 synthesizer 的汇总计数,但**没有任何门控依赖它**。我把它当占位字段,不拿它当卖点。
- 真要让它有意义,需要 citation context——要么抓全文(法律和成本问题),要么用 Semantic Scholar 的 citation intent API。

**Q3. 记忆校准会不会把错误放大?怎么防?**
- 会。Playground 污染事件:analyzer 把一个不相关但 support=94 的 pattern 判成相关,基线 0.75 被抬到 0.85+。
- 防线:(1) prompt 要求先独立打基线分再做校准,两个数都输出,delta 可见;(2) per-run 指令过滤 pattern 类型;(3) `support_count` 是连续量、边际递减,不是开关,单条 pattern 抬不到天花板;(4) curator 定期清理低质 pattern,基率来源干净。
- 没做的:没有"记忆校准 delta 超过 0.3 就标记人工复核"这种上限门。这是我最想补的一条。

**Q4. LLM 输出 JSON 格式错了怎么办?语义错了怎么办?**
- 格式错:schema 门 + 重试,确定性解决。
- 语义错(格式对、内容错):这是真正难的。现有手段是 golden 测试(T1 的 4 个 golden 文件)防回归,A/B 评估防记忆退化,negative control 病例防误检索。没有在线的语义校验。
- 如果做:用 flash 做一个便宜的 judge agent 复核 pro 的 diff(相当于 LLM-as-judge),不一致时标 `needs_review`。

**Q5. 通知发错了(假阳性)有什么后果?怎么减少?**
- 后果是骚扰真实研究者、损害信誉。所以 demo 阶段 fallback 到团队邮箱是有意的安全设计,不只是"没拿到邮箱"。
- 105 条假阳性事件没有发出真实邮件,正因为这层。
- 减少:数据侧(abstract 占位符检测已加)+ 分数门(待加)+ 人工队列(待加)。

**Q6. 三个索引 dry-run-by-default 的 curator——为什么写工具要默认不写?**
- pattern 是所有后续判断的基率来源,污染它的代价是放大的。curator 的 merge 用 Gemini 判断"这两条是同一现象吗",schema 门通过才写;E3 探针证明它对植入的重复对和垃圾行处理正确,conservative default 成立。
- unbounded-apply 事故的教训:治理工具的默认值必须是"不动数据"。

**Q7. 如果 analyzer 挂了(429 那种),用户看到什么?**
- 前端 SSE 看到 extractor 亮、后面不亮,流结束。没有明确的"失败"帧——这是 Agent Engine 静默终止流的副作用,是已知缺口。
- 应该做:dispatcher 在流异常结束且没拿到 drift_event 时写一条 `status=failed` 的记录并推一个 `pipeline.failed` 事件。

**Q8. 有没有"置信度"字段?为什么没有?**
- 没有独立的 confidence 字段。原因是 LLM 自报的置信度和真实准确率相关性弱,加了会被误用。`materiality_score` 是严重度不是置信度;`severity_calibration.evidence[].support_count` 是最接近"证据强度"的量。
- 如果要:用 self-consistency(同一输入采样 3 次看 diff_type 一致率)作为置信度代理,比让模型自报靠谱。

**Q9. 人工审核如果做,怎么设计?**
- 队列放 ES(`review_queue` 索引),入队条件:materiality ≥0.7 或 calibration delta ≥0.3 或 diff_type=claim_reversed;前端加一个 review 视图,approve 后才触发 notifier;审核结果回写成标注,喂 E2/E5 评估集。
- 关键是**审核在 notifier 之前而不是之后**——通知是不可撤回的动作。

**Q10. 你怎么定义这个系统的"可靠"?**
- 三条:不发错(假阳性率,靠数据质量门 + 分数门)、不漏(幂等 + watermark + Pub/Sub 至少一次)、可审计(每个判断都能回溯到 pattern id、support_count、rationale、agent_events 流)。现在第三条做得最好,第一条靠 fallback 邮箱兜着,第二条有已知的非原子窗口。

---

## 方向 5:海量数据抓取与反爬策略

> 图中原题:bioRxiv/medRxiv 是否提供官方 API?面对速率限制如何设计增量抓取与缓存机制?如何应对反爬验证?

### 核心答案(2 分钟版)

**先纠正前提**:四个数据源**全部是官方公开 API**,没有爬网页,所以不存在"反爬验证"要绕。反过来,我们的义务是**做个礼貌的客户端**——这是学术 API 生态的规则(polite pool)。

| 源 | API | 用途 |
|---|---|---|
| bioRxiv / medRxiv | `api.biorxiv.org/details/{server}/{from}/{to}/{cursor}`,每页 100,cursor 分页 | 拉 preprint + `published` 字段 |
| Crossref | `api.crossref.org/works/{doi}`,`relation.is-preprint-of` | 把 preprint 配对到正式发表版 |
| OpenAlex | `works?filter=cites:{id}` | 找引用它的下游论文(按需,不预载) |

**增量抓取**:Cloud Run Job + Cloud Scheduler 每小时,`--since` 窗口 + `--limit 300`;历史回填是一次性的宽窗口(2023-01-01, limit 4000),运维文档明确"别把回填参数留在定时任务上"。三个 Job 错峰 0/10/30 分。

**去重和缓存**:ES 本身就是缓存。文档 id 是 `{normalized_doi}::{version}`,写入全是 upsert,重复拉取幂等。Crossref 配对只扫"还没有 published_doi 的 preprint"(`fetch_unpaired_preprints`),工作量有界。OpenAlex 200M 篇不可能预载,按需查、`per_page=25` 封顶。

**速率限制**:统一在 `PullerBase._get_json`:User-Agent 带联系方式、30s 超时、3 次重试 + 线性退避。诚实说这是够用级别,不是生产级——见 Q3。

**规模**:10,067 真实 preprint、2,229 对,满足 B 侧目标(≥10k / ≥500)。

### 10 个可能的追问

**Q1. bioRxiv API 的坑?**
- 它没有"按更新时间"过滤,只有日期区间 + cursor,所以增量靠 `--since` 日期窗口,窗口内全拉再靠 upsert 去重;`total` 字段有时缺,循环终止条件要同时看"这页新增 0 条"和 `cursor >= total`。
- 一个 preprint 多个版本(v1/v2/v3),我们只关心最终版 → `is_final_preprint` 标记,Crossref 配对时写回。

**Q2. 为什么用 Crossref 配对而不是 bioRxiv 自带的 `published` 字段?**
- 两个都用。bioRxiv 的 `published` 字段有滞后和缺失;Crossref 的 `is-preprint-of` 关系更权威。`--include-published` 时先用 bioRxiv 的字段,`crossref-batch` 再补一遍。
- 配对是整条流水线的触发条件(有 `published_doi` 才会 dispatch),所以它的召回直接决定系统有多少活干。

**Q3. 速率限制具体怎么处理?429 呢?**
- 现状:`retry=3, backoff=1.0`,`sleep(backoff * attempt)` 线性;HTTPError 不区分 429 和 500;没读 `Retry-After`。
- 为什么够用:单 Job 单线程串行,每小时 300 条,远低于三家的 polite pool 限额(OpenAlex 10 rps / 10 万天;Crossref polite pool ~50 rps;bioRxiv 未公布但同量级)。
- 生产级要改:429/503 走指数退避 + jitter + 尊重 `Retry-After`;4xx 其他不重试;多 Job 并发时要一个共享的令牌桶(Redis 或 ES 文档做计数)。

**Q4. "礼貌客户端"具体做了什么?**
- User-Agent 里带项目名 + 联系方式(Crossref/OpenAlex 据此把你放进 polite pool,限额更松、更稳定);串行请求不并发;错峰调度;回填一次性、增量小窗口。
- OpenAlex 还支持 `mailto=` 参数,我们放在 UA 里,效果一样。

**Q5. 如果对方封了你(403/ban)怎么办?**
- 学术 API 一般不 ban 礼貌客户端,更常见的是被降到匿名池限速。对策是联系方式真实可达 + 申请 API key(OpenAlex 有 premium)。
- 绝不做:换 IP、伪造 UA、绕验证。这是学术基础设施,毁掉的是自己的信誉。

**Q6. 数据质量出过什么问题?**
- ⭐ published 行 abstract 缺失事件:Crossref 对很多出版社不返回 abstract,`records.py` 当时把 title 回退进 abstract 字段;analyzer 拿到"一句话"的 published 版,把 preprint 所有 claim 判成 disappeared,材料性虚高 → 105 条假阳性 drift_event,污染 dashboard 平均分。
- 闭环四层:止血(不回退)+ 下架 105 条 + dispatcher 写入前 `published_abstract_is_placeholder` 检测自动打标 + BFF 过滤。P2 的历史回填没做(要重拉 abstract)。
- 教训:**上游字段缺失要显式 null,不要用"看起来合理"的值填**——LLM 对合理的垃圾没有免疫力。

**Q7. 拉下来的数据怎么进 ES?批量还是单条?**
- `bulk` upsert,batch 250-500;DOI 归一化(小写、去 `https://doi.org/`);`record_source` 字段在写入时打标区分 demo 种子和真实数据,所有真实数据视图 `must_not term record_source=demo_seed`。

**Q8. 触发流水线的"新 pair"怎么发现的?会不会漏?**
- Elastic Scheduled Workflow 每 5 分钟:读 `dispatch_state.last_seen_ingested_at` 水位 → 查 `published_doi exists AND is_final_preprint AND ingested_at > 水位 AND 非 demo AND 非 published 行`,按 `ingested_at` 降序 `size:20` → 逐条 POST `/dispatch` → 水位推到 `hits[0].ingested_at`。
- 会漏的场景:一个 tick 里新 pair >20 条,水位推到最新那条,中间的永远不会再查到。修法是水位推到**本批最老那条**或者 `size` 放大 + 分页。这是已知的边界。
- 另一个真实事故:ES API key 静默过期 → workflow 报 `security_exception`,一度误以为是 Gmail 问题。key 过期不可续只能换,换完要重新 upsert workflow 重绑运行时身份。

**Q9. 为什么 OpenAlex 不预载到 ES?**
- 200M 篇 works,预载没意义;我们只需要"谁引用了这一篇",按需两次 HTTP 就够(先 DOI → OpenAlex 短 id,再 `cites:` 过滤)。
- 实现是 Elastic Workflow YAML 的两个 `http` step,通过 MCP 暴露给 citation_finder。选 YAML 而不是 Python FunctionTool 是为了 5/5 agent 都走同一个工具面。踩过 Liquid `cgi_escape` 不工作(靠 OpenAlex 服务端路径归一化)和 http step 输出在 `.data` 不在 `.body` 两个坑。

**Q10. arXiv 为什么砍了?**
- OAI-PMH 是 XML 协议,解析和分页复杂度明显高于三家 REST;bioRxiv + medRxiv 已经 10k 篇,够验证 memory loop。4 周里砍范围比多一个源重要。写进 changelog 2026-05-26。

---

## 方向 6:通知可靠性与异常监控

> 图中原题:使用 Gmail API 发送通知遇到限流或被判定为垃圾邮件时,系统是否有监控告警?是否实现了指数退避的自动重试机制?

### 核心答案(2 分钟版)——分层诚实

**发信路径**:notifier 只起草(status=drafted),真正发送是 dispatcher 的 `send_and_update`:OAuth refresh token 存 Secret Manager、懒加载 Gmail v1 service、`users.messages.send`,结果写回 `notification_log.status`(sent / failed / skipped + error_message + sent_at)。每封邮件独立 try/except,一封失败不影响其他。

**指数退避重试——分两层回答**:
- **agent 调用层有**:supervisor 的 `RetryPolicy`——3 次尝试、每次 90s 超时、1s 起步 ×2 指数、30s 封顶、可配 jitter;失败尝试的事件被缓冲不泄漏。
- **Gmail 发送层没有**:`HttpError` 直接记 failed,不重试。理由是当时的规模(demo 一次 4 封,发到自己的测试邮箱)没触发过限流;而且**发送不幂等**——重试有重复发信风险,需要先有 send-level 去重再加重试。这是明确的技术债。

**垃圾邮件判定 / 退信**:没有检测。Gmail API 发送成功≠送达,退信会以一封 mailer-daemon 邮件回到发件箱,我们没有读它。

**监控告警**:有可观测(Cloud Run 日志、ReasoningEngine 运行时日志、`notification_log` 可查、`agent_events` SSE 流可回放),**没有告警**(没有 Cloud Monitoring alert policy)。429 事件是人工翻日志发现的。

**如果做生产版**:见 Q5-Q7,核心是"幂等 + 退避 + 死信 + 失败率告警 + 退信回读"五件套。

### 10 个可能的追问

**Q1. 为什么发送放在 dispatcher 而不是 notifier agent 里?**
- 副作用要归确定性代码,不归 LLM:agent 输出可以重跑、可以在 Playground 测,发邮件不能。契约 §3.4.2 明确 v0 agent 只 draft,`dispatch.status` 由 orchestrator 填。
- 也方便替换通道(SES/Postmark)而不动 agent。

**Q2. Gmail API 的限额是多少?会不会撞?**
- 消费者账号约 500 封/天,Workspace 约 2000 封/天;API 层还有 per-user 每秒配额。demo 量级(一次 ≤25 封)撞不到。
- 撞到的表现是 `HttpError 429 rateLimitExceeded` / `403 dailyLimitExceeded`,现在会记 failed 然后丢。

**Q3. 指数退避你在哪里实现过?参数怎么选的?**
- `hardening.py`:`backoff_for(i) = min(base × mult^i, cap)`,默认 1s/2×/30s 封顶,`jitter` 默认 0 让测试可复现,生产可传小数去相关。
- 参数理由:Agent Engine 的瞬时抖动(grpc UNAVAILABLE)几秒内恢复,1-2-4s 够;30s 封顶是因为 dispatcher 整体预算 600s,3 次重试 + 3 次 90s 超时不能吃光它。
- 测试方式:注入 fake call_factory,不碰网络。

**Q4. 为什么 Gmail 发送没加同样的退避?你不觉得矛盾吗?**
- 承认是不一致。区别在语义:agent 调用是幂等的(重跑只是多花 token),发信不是。给发信加重试前必须先做"这个 `affected_citation_id` 已经 sent 就不再发"的检查——现在 `notification_log` 的 `_id` 就是 `affected_citation_id`,状态字段已经有了,只差在 `_send_sync` 前读一次。补上之后再加退避才安全。

**Q5. 生产版的发信可靠性你会怎么设计?**
1. **幂等**:发前查 `notification_log.status == sent` 跳过;Gmail 支持 `Message-ID` 头,可用 `affected_citation_id` 派生,便于对账。
2. **重试分类**:429/5xx 指数退避 + jitter + 尊重 `Retry-After`;401/403 不重试(凭证问题,告警);400 不重试(内容问题)。
3. **死信**:超过 N 次进 `notification_dlq`,人工处理。
4. **异步化**:每封邮件一条 Pub/Sub 消息,消费者限速,Cloud Run 横向扩。
5. **对账**:定时任务扫 `drafted` 超过 1h 未 sent 的行。

**Q6. 垃圾邮件 / 退信怎么监控?**
- Gmail 没有 webhook,退信是 mailer-daemon 邮件。做法:定时读发件账号收件箱里 `from:mailer-daemon` 的邮件,解析原 Message-ID 回写 `status=bounced`。
- 更靠谱的是换专业通道(SES/Postmark/SendGrid)——有退信/投诉 webhook、有信誉仪表盘;Gmail API 适合 demo 不适合批量通知。
- 防进垃圾箱:自有域名 + SPF/DKIM/DMARC、内容不含营销词、明确退订链接、发送量渐进。邮件正文已经是中性告知语气 + 免责声明,这部分是对的。

**Q7. 告警你会怎么配?**
- Cloud Monitoring 基于日志的指标:`gmail send failed` 计数、`sub-agent failed` 计数、`/run` 5xx;alert policy:5 分钟内 failed/sent > 20% 或任何 401/403 立即告警;ReasoningEngine 429 计数 > 10/小时告警(这条会直接抓到那次两小时 50 次的事件)。
- 加 `/run` 的 P95 时长告警(>400s 说明重试在堆)。

**Q8. 现在出了问题你怎么发现的?**
- 三条路:Cloud Run 日志(`log.exception` 有栈)、`notification_log` 里 `status=failed` 的行(BFF 能查)、Agent Engine 运行时日志(429 就是这里看到的)。
- 真实经历:一次 grpc UNAVAILABLE 503 让整条流水线静默中止,`notification_log` 里什么都没有——因为还没走到写入。这催生了"失败也要写一条记录"的 TODO。

**Q9. Pub/Sub 那一跳的重试语义是什么?**
- push 订阅、ack-deadline 600s;`/run` 返回 2xx 才 ack。设计上**故意**对"按设计丢弃"的失败(payload 解析错、流水线内部异常)也返回 2xx——因为这些重试也不会成功,返回 5xx 只会让 Pub/Sub 无限重推。只有真正的瞬时错误才该 5xx,目前没有这类分支。
- 加上 `/dispatch` 的幂等门,整体是"至少一次投递 + 应用层去重"。

**Q10. 收件人邮箱从哪来?隐私怎么处理?**
- OpenAlex 不给邮箱,`citing_paper_authors[].email` 始终 null,所以现在所有邮件走 `DEMO_FALLBACK_EMAIL`。生产要接 ORCID 公开邮箱或论文通讯作者邮箱,并且加退订与来源说明。这也是为什么"是否真发给研究者"被明确留在 v1 范围外——发错的成本远高于不发。

---

## 方向 7:回顾——如果有更多时间,哪些地方可以做得更好?

> 面试原题(retrospect 类):"如果这个项目有更多时间去做,哪些地方可以做得更好?" / "你觉得这个项目有哪些未来可以改进的点?"
>
> 讲法:开场说分 feature / tech / code 三块,**每块挑一个细节 + 一个野心**(⭐ 标记的是引入 LLM / 有挑战性的点),不要全念。排序原则:离产品核心主张("记忆让严重度判断越来越准")越近越靠前;"通知"是唯一不可撤回的动作,它的闸门排第二;工程和规模排第三。

### Feature(prototype → 产品)

**用户与商业**
1. **注册 / 身份认证**:现在仪表盘匿名公开、邮件发到团队测试邮箱。学术用户用 **ORCID OAuth** 登录(一登录就拿到他的论文列表,直接知道他引用了哪些 preprint),机构用户走 SSO;Cloud Run 前加 Identity Platform / IAP,BFF 从"公开只读"变成"按用户过滤"。
2. **订阅模型**:订阅对象可以是 DOI、作者、领域标签、或"我 ORCID 下所有论文引用的 preprint"。通知从"系统决定发给谁"变成"用户决定关注什么"——同时解决了收件人邮箱拿不到的问题。
3. **付费分层**:免费档看仪表盘 + 关注 10 个 DOI;Pro 档无限关注 + 实时邮件 + API key;机构档(图书馆、期刊社)整域名覆盖 + 审计导出。Stripe 计费,配额落在 BFF 用户表。
4. **like / dislike 反馈**:每封通知带"这个判断有用 / 没用",回写 `drift_patterns`——**同时是产品功能和 memory loop 缺失的反馈环**,一个按钮两个用途。
5. **通知偏好**:即时 / 每日 / 每周摘要;只收 materiality ≥ X;退订(发信合规底线)。
6. **rate limiting + 防滥用**:API key 限速;同用户同 DOI 通知去重;`?force=true` 类接口不对外。

**核心能力延伸(野心)**
7. ⭐ **引用上下文提取**:citation_finder 现在只看标题,`severity_tier` 无意义。接 Semantic Scholar citation intent,或对 OA 全文用 LLM 定位"引用这篇 preprint 的那句话",判断核心依据 vs 背景提及——让"谁受影响最大"从猜测变证据。
8. ⭐ **图表级 drift**:preprint 和发表版的图变了(森林图、生存曲线)而摘要没变——多模态 Gemini 比对图像。相当比例的实质修改只在图表。
9. ⭐ **claim 谱系图**:被修正的 claim 被谁引用、那些论文又被谁引用——"受污染传播链"知识图谱,给期刊和综述作者看"这个结论的上游已经动摇"。
10. ⭐ **自动起草更新建议 / erratum**:通知不只说"变了",为引用者起草"如果要更新相关段落,建议改成…",一键接受。
11. **Zotero / Mendeley 插件**(契约 §1.3 已列):在用户文献库里直接给受影响条目打标。
12. **期刊 / 审稿人侧产品**:投稿时自动比对该稿的 preprint 版本,列出实质变化让审稿人核对是否已声明——另一个付费客户群。
13. **多语言、多领域**:现在只有英文生物医学;arXiv(被砍)、SSRN、中文平台是扩展面;ELSER 只支持英文,多语言要换 E5 / Gemini embedding。

### Tech

1. **反馈环闭合**(feature 4 的后端):pattern 增加 `confirmed_harmful / confirmed_benign`,`support_count` 变成有方向的证据;用真实反馈校准 prompt 里从 COMPare 借来的因果先验。
2. **发信闸门 + 人工复核队列**:写 drift_events 时算 `review_flags[]`(delta ≥ 0.3 且 support ≤ 2、跨类型采纳、所有 claim 消失、materiality < 0.3),命中即进队列不进 notifier。
3. **幂等门移到 `/run` 消费端 + in-flight 锁**;Gmail send-level 去重(状态检查 + 确定性 Message-ID)之后再加指数退避;退信回读;或换 SES / Postmark 拿投递回调。
4. **告警**:429 计数、发信失败率、`/run` P95;流静默中止时写 `pipeline.failed`。
5. **扩缩形态**:push + concurrency=1 → worker pool + CREMA 按 Pub/Sub 积压扩,concurrency 5-10,以模型配额封顶;analyzer 上 Provisioned Throughput。
6. **检索**:线上从 ELSER-only 换真混合(ES|QL 不支持 RRF,改 Workflow 工具调 `_search`);统一三套枚举后加 `pattern_type` 预过滤;注入干扰做 IR 评估;top_k 10 → 3。
7. ⭐ **置信度代理**:self-consistency(同输入采样 3 次看 diff_type 一致率)或 flash judge 复核 pro 的 diff——现在没有任何 confidence 信号。
8. **减 latency / 加 cache**:`/api/stats` 聚合结果缓存 5s;pattern 检索按描述子哈希缓存,同 pair 重跑不再打 ELSER。
9. **存储分层**:`agent_events` 高写入低查询价值,挪到时序存储 / BigQuery,ES 只留需要检索的。
10. **数据侧**:水位推到本批最老(修 size:20 丢数据);polite pool 加 mailto;429 与 5xx 区分退避;P2 回填 published 摘要。

### Code

1. **映射文件补齐**:`severity_calibration`、`suspected_false_positive` 线上带外扩的,JSON 没跟上——审计脚本抓的正是这种漂移。
2. **prompt 版本化**:INSTRUCTION 加版本号写进 drift_events;线上跑的是部署时打包的版本,曾因此排错方向。
3. **BFF 换 FastAPI**:从 mock server 长出来的 `http.server`,SSE 每连接一线程;和 dispatcher 统一框架。
4. **dispatcher 邮件发送 `gather`**:现在串行 await。
5. **memory_synthesizer 真异步**:现在在 supervisor 末尾 await,主流程多等 40s。
6. **工具定义文档滞后**:`search_drift_patterns.json` 参数描述还是 v0 的"joined claim texts"。
7. **测试覆盖 N>0**:T1 的 4 个 bug 全因冒烟只测 N=0;golden 加一个 N>0 固定 pair 进 CI。
8. **处理 `citation_finder.severity_tier`**:删掉或明确标为占位,没有下游用它,留着误导。

### 收尾句

"如果只能做一件,做反馈环——它不是最难的,但它决定了后面所有优化是在校准一个真实信号,还是在放大一个借来的假设。另外有一件事我**不会**做:在反馈环之前继续加 agent 或加功能。"

---

## 附:一页速查表

| 追问方向 | 一句话锚点 | 关键数字 | 诚实缺口 |
|---|---|---|---|
| 多 Agent | agent 之间零通信,星形;supervisor 纯代码同步调用每个子 agent;Pub/Sub 只在入口一跳 | 60s vs 200s;T1 4 个 bug 5 轮 | 幂等非原子;synthesizer 未真异步 |
| 冷启动 | min-instances=1 是必需不是优化;120s 卡顿是 GFE 缓冲不是冷启 | 40s→10-20s;ready 151s→0.2s | Agent Engine 冷启不可控 |
| 语义检索 | ELSER 因为同一存储 + 稀疏零样本 + 可混合;`_score` 不能当阈值 | 35 条 pattern;0.75 阈值废除;E4 rank 1 | 无 IR benchmark |
| 低置信度 | 无自动降级;靠可审计 + schema 门 + 邮件免责 + fallback 邮箱 | 105 条假阳性四层闭环 | 无人工队列;无分数门 |
| 数据抓取 | 全官方 API,礼貌客户端;ES 即缓存,upsert 幂等 | 10,067 / 2,229;每小时 300 | 线性退避不分 429;水位 size:20 可能漏 |
| 通知可靠性 | agent 层有指数退避,Gmail 层无;有日志无告警 | 3 次/90s/1s×2/30s 封顶 | 无退信检测;发送不幂等 |
