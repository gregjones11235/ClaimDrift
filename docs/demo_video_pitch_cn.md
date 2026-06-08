# ClaimDrift — 演示视频设计简报

**ClaimDrift**

演示视频 — 设计简报

*因为科学不应在沉默中悄然漂移。*

**影响力（Impact）**   **技术（Tech）**   **设计（Design）**   **创意（Idea）**

Google Cloud ADK Hackathon  ·  3:00 硬性上限（目标 2:50）

# 1. 视频构思

三幕式结构，总时长约 2:50。整体节奏特意让四项评审标准各落一拍：**问题（Impact）→ 真实运行（Tech）→ 自我改进（Idea）**。

**第一幕 — 动画化的问题与价值（0:00–0:35）。** 一段简短的动态图形序列将痛点戏剧化：一篇预印本所声称的发现（"55 个全基因组显著基因、92 个致病变异"），在同行评审过程中悄悄地缩水到"15 个基因、16 个变异"，而每一篇引用它的论文却依旧"亮着灯"、浑然不觉。画面最终收束为 Logo 与一句价值承诺。

**转场（0:35–0:36）。** 一个 1 秒的几何转场配以"嗖"的音效，从扁平动画切入真实浏览器。**背景音乐从此处开始，并持续贯穿视频余下全部内容。**

**第二幕 — 实时演示，出镜呈现（0:36–2:50）。** 真人主持或旁白带领观众实时走过产品的真实运作过程——架构图、逐步点亮的智能体流水线、漂移证据、自动发送的通知，以及自我改进的高潮。所呈现的一切都是真实的，全程无需人工介入即被检测出来。

**一句话字幕：** *"五个运行在 Vertex AI 上的 Gemini 智能体，以 Elastic 作为基于 MCP 的上下文 + 记忆层，构成一个能从每一次发现的漂移中学习的闭环。"*

# 2. 时间线

| **#** | **时间** | **节拍** | **画面 / 屏幕内容** | **命中评审项** |
| --- | --- | --- | --- | --- |
| **S1** | 0:00–0:18 | 问题（动画） | 一篇 POTS 全外显子测序预印本高调声称"55 个全基因组显著基因、92 个致病变异"；众多论文引用它；同行评审后数字悄然缩水到"15 个基因、16 个变异"，引用却依旧亮着、毫不知情。 | Impact |
| **S2** | 0:18–0:35 | 价值（动画→Logo） | 引用链接逐条变红；Logo + 标语；数据源滑入（bioRxiv·medRxiv·Crossref·OpenAlex）。 | Impact + Idea |
| **🔀** | 0:35–0:36 | 转场 | 1 秒几何转场 + 嗖声音效；扁平动画切入真实浏览器。（背景音乐开始。） | — |
| **S3** | 0:36–0:58 | 概览 + 架构 | 真人画外音 / 旁白开始。展示架构图（数据流沿闭环走一圈），然后切到真实 dashboard——顶部四个真实徽章：**Tracked events 389**（146 条 ≥0.7 高严重度）、**Avg materiality_score 0.49**（across 389 events）、**Affected citations 796**（742/747 emails sent）、**Patterns learned 34**。下方是真实漂移事件列表。（具体数字以录制当下前端实际显示为准。） | Design + Tech |
| **S4** | 0:58–1:24 | 流水线运行（现场实时） | **/playground/orchestration**——点 RUN 现场实时跑完整的 5 智能体编排（Claim Extractor → Drift Analyzer → Citation Finder → Notifier → Memory Synthesizer）。横向流水线随真实 SSE 事件逐个**实时点亮**（started/active/done），最后一个节点写回提炼模式、自动发出通知邮件。全程真实、无人工介入、评委可亲手触发。 | Tech |
| **S5** | 1:24–1:44 | 漂移证据 | /event/[id] 显示声明差异红色高亮 + 真实 materiality_score；/citations 展示按严重度排序的真实 OpenAlex 受影响论文。（具体数字以录制时前端实际显示为准——录真实抓取的那条漂移事件，不用 demo_seed 假数据。） | Tech + Impact |
| **S6** | 1:44–2:02 | 通知 | /notifications；自动起草并已发送的 Gmail，引用完整句子 + 链接；每位受影响作者一封邮件。 | Tech + Design |
| **S7** | 2:02–2:38 | 高潮：自我改进（A/B 测试） | 切到 **/playground/memory-ab** 交互页，点 RUN 现场跑 A/B 测试：同一篇真实的、本身模棱两可的漂移（NCT01163032 Tasimelteon 临床试验，预设的共同主要终点被降级为次级"step-down"终点），用同一个生产 prompt 跑**三张并排状态卡——无记忆 / support=5 / support=20**。**无记忆**时分析器只能说"这件事本身重要"（materiality 0.75，rationale 里写 *intrinsic significance*）；**召回记忆后**它认出"这是一种已知的、反复用来粉饰成功的操纵套路"（0.85），且证据越强措辞越笃定（support 5 *increase confidence* → support 20 *strong support*）。**数字封顶 0.85 恰恰证明系统有合理上限、不为好看而无限加分**——价值在 rationale 的推理升级，不在数字虚涨。 | Idea + Tech |
| **S8** | 2:38–3:00 | 收尾 | 快速回顾架构图；金色记忆闭环边线最后高亮；Logo + 标语 + 仓库 / 托管 URL。 | All |

**注：** 上限设为 2:50 以留缓冲（仅前 3:00 会被评审）。**S4** 在 **/playground/orchestration** 现场实时跑真实的 5 智能体编排（点 RUN 即触发，节点随真实 SSE 逐个点亮）；如需稳定、可复现、无需实时 GCP 凭证的备用录制，BFF 仍支持 **SSE_REPLAY_GOLDEN=1** 的"黄金"事件流（走 /live 的 AgentTimeline 视图）作为后备。**S7** 的 A/B 测试是真实数据：三种记忆条件都用同一个生产 `drift_analyzer` prompt 在真实 Elasticsearch 上跑出，分析器的 support_count 是从检索到的 pattern 里读出的（case 输入从不告诉它数字），所以 rationale 的变化只可能来自召回的历史基率，不是写死在 prompt 里——这是对"你是不是把结论写进了 prompt"最强的回答。强制配英文字幕；第二幕全程使用免版税背景音乐。

# 3. 系统架构

在 S3 首次以全局图呈现，并在收尾（S8）时再次调出，金色记忆边线最后高亮。最具分量的两个要点：**Google Cloud × Elastic 双栈**，以及**自我改进的记忆闭环**（金色边线）——Memory Synthesizer（记忆合成器）写入一条提炼后的模式，供 Drift Analyzer 在下一次运行时召回。

*五个运行在 Vertex AI 上的 Gemini 智能体；以 Elasticsearch 作为通过 MCP 访问的上下文 + 记忆层；一个闭环，其中 Memory Synthesizer 写入提炼后的模式，供 Drift Analyzer 在下一次运行时召回。*

