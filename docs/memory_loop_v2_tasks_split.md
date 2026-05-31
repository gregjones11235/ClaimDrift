# Memory Loop v2 — 双人分工任务包（role C × role B）

Last updated: 2026-05-30（merge 后校正）
配套设计文档：[memory_loop_v2_design.md](memory_loop_v2_design.md)（决策与依据的唯一真相源）

本文件把 v2 的实现任务（设计文档 Part D/E）切成两份可并行、交界面清晰的任务包，
供 role C（架构 + GCP owner）与 role B（实验 + prompt + scorer）配合完成。

> **当前基线（2026-05-30 merge 定稿）**：Part C 契约已随本次 merge 落地到
> [contracts.md](contracts.md) 的 `main`。其中 §3.6 pattern_curator、§2.2.5 双端点、
> §3.5 治理移交由 role C 写入；`severity_calibration` 字段 schema 在 merge 中**以 role B
> （Jeremy）版为准**（字段：`baseline_materiality_without_memory` / `calibrated_materiality`
> / `calibration_delta` / `memory_pattern_ids` / `evidence[]` / `rationale`），`outcome_switch`
> 已进 `pattern_type` 枚举与 drift type 表。
>
> **进度更新（2026-05-31 晚）：E1/E3/E4 全部跑通，检索栈修复落地，剩 E2。**
> - **泳道 A（role C）**：C1/C2/C3 全部落地 + 真集群验证（详见下文 ✅）。
> - **泳道 B（role B / Jeremy）**：B1/B2/B4 均已完成。
> - **Part E 实验进度（2026-05-31 由 role C 单人推进，role B 不在场）**：
>   - **E1 ✅**（2026-05-30 已跑通 PASS：baseline 0.75 → treatment 1.0，delta 0.25）。
>   - **E3 ✅**（curator 正确性 `_e2e_probe.py` 全过；新增 `_e3_latency_probe.py` 证明 curator
>     满载时实时检索 p95 仅 +2.3%）。**注：文档原说"E3 需两人"，但 role C 一人持 ES/GCP
>     权限即可单跑——已单人完成。**
>   - **E4 ✅**（两层：`e4_generalization_probe.py` 检索层 + `e4_e2e_setup/check.py` 端到端，
>     证明去写死 hint 后泛化到 fixture 外领域，agent 不误用临床记忆）。
>   - **E2 ⏳ 进行中**（见下方「E2 收尾计划」——核心 demo 镜头，待真实案例抓取 + 受控曲线）。
> - **额外修复（demo 之外的真实收益）**：诊断并修复了 RRF "假 hybrid"（ELSER 自融合，词法路径
>   缺失，2026-05-23 遗留），用 copy_to 词法镜像原地改造主索引恢复真混合检索；中途纠正了误用
>   `rebuild` 影子索引的问题，系统已回稳态（alias→drift_patterns，读写统一）。详见 contracts.md
>   2026-05-31 三条 changelog + [[project_hybrid_retrieval_fix]]。
> - 非阻塞收尾：B3（curator prompt 调优）。

---

## 分工原则（本次拍板）

- **按专长切，不按任务序号切。** role C 扛全部基础设施与确定性代码；role B 扛全部
  实验、prompt 与 scorer。
- **AI 触点尽量归口**：curator 内部那一次 LLM 调用（“是否同一现象?” + description 改写）
  的 *骨架、护栏、以及默认 prompt* 都由 role C 一并交付——默认 prompt 已写入
  contracts.md §3.6.4（不留空位）。role B 若想调优可在其之上迭代，但不阻塞任何人。
- **契约已 merge 定稿**：设计文档 Part C 的 5 处改动已随 2026-05-30 merge 全部落地
  [contracts.md](contracts.md) 的 `main`（§2.2.5 双端点 + `outcome_switch` 枚举、§3.2.2
  `severity_calibration`、§3.5 治理移交说明、新增 §3.6 pattern_curator 含 §3.6.3 I/O
  schema 与 §3.6.4 默认 prompt）。两条泳道现在可直接照契约并行，无需再开“阶段 0”。
  - **merge 取舍记录**：§3.6 / 双端点 / §3.5 取 role C 版；`severity_calibration` 字段与
    `outcome_switch` drift type 表取 role B（Jeremy）版——见顶部「当前基线」块。role B 已基于
    其字段写了 `drift_analyzer/agent.py` 与 eval，故 role C 的 C 系列代码须照 Jeremy 的字段名落地。

> 契约已是唯一真相源。role B 如需调整自己领域内的契约（如 `pattern_type` 枚举值、
> `severity_calibration` 字段细节），自行改 contracts.md 即可，改后在群里同步 role C。

---

## 泳道 A — role C（基础设施 / 确定性代码 / GCP / ES）✅ **全部完成（2026-05-31）**

> 全部是确定性、可测试、零幻觉的工作；唯一的 LLM 触点（curator 内部判断）只搭骨架与护栏，
> prompt 文本交给 role B（见交界面 H2）。
>
> **C1/C2/C3 已全部落地、测试、真集群验证通过**（回归 74 过）。运营/事件复盘见
> contracts.md §3.6.5 与 [pattern_curator_ops.md](pattern_curator_ops.md)。

### C1 — D4：专属 ELSER 端点 + 影子索引重建 ✅
- [x] 创建 `claimdrift-elser-batch` 推理端点（EIS `service:elastic` + `model_id:elser_model_2`，独立容量）。
- [x] 建影子索引 `drift_patterns_v2`（`pattern_description` 绑 `claimdrift-elser-batch`）；
      `manage_pattern_alias.py` 实现 read alias `drift_patterns_read` + 原子 swap 蓝绿重建 + rollback；
      实时读路径（`elastic_retrieval.py` + ES|QL 工具）改经 alias。
- [x] 保留 keyword 预过滤 + 分批/限流 + 错峰触发作为互补降载手段。
- 端到端验证：reindex 82 条经 batch 端点重 embedding → swap → 检索仍准 → rollback，全过。

### C2 — D5：Supervisor 加固（纯代码，不用 AI）✅
- [x] `hardening.py` 在每次子 agent 调用周围加 schema 校验（jsonschema）+ backoff 重试 + 超时；
      `schemas.py` 定 §3.x.2 最小 schema；agent.py 接入**分阶段降级**（核心链 fail-fast /
      notifier 跳过 / memory_synthesizer 非阻塞）。
- [x] 维持 `supervisor_agent` “NOT an LLM agent”；零 LLM 逻辑。15 个单元测试。

### C3 — D3：pattern_curator 批处理作业 ✅
- [x] 独立 Cloud Run Job（**不是 LlmAgent**），增量扫描（`last_updated_at` 高水位）。
- [x] 确定性部分：keyword 预过滤、卫生（**两层**：UUID 结构正则 + drift_events 引用完整性、补时间戳、
      重算 `support_count`）、过滤式淘汰（复用 cleanup 定向删除模式）。
- [x] LLM 判重接线：genai 结构化输出（Vertex `response_schema`）+ jsonschema 闸 + 提议→校验→写入
      + 保守默认（非 high confidence 不合并）。
- [x] 乐观并发写（`if_seq_no`/`if_primary_term`）。**对去重 + `max_judgments=50` 封顶 + 进度日志**
      （2026-05-31 失控修复）。**dry-run-by-default 运营模型**（调度只提议，apply 需人工 review）。
- role B 随后做 B3：把 §3.6.4 默认 prompt 调优到产品质量。

---

## 泳道 B — role B（实验 / fixture / prompt / scorer）

> 全部是 B 已有经验的工作（B 此前做过 v1 A/B、写过 scorer、回灌过 prompt）。

### B1 — D1：旗舰 fixture（**先做，是 D2/E1 的前置**）
- [ ] 向 [memory_loop_ab_cases.json](../agents/evals/memory_loop_ab_cases.json) 增加
      primary-outcome-switch A/B fixture：seed（一次结局偷换）、treatment（相似偷换）、
      负对照（无关良性改动）。
- [ ] **关键设计约束**：baseline 无法校准严重性（只看到单个 diff），treatment 可以（用到基率）。
      拍摄/跑实验前先验证这个差距真实存在。
- 依赖阶段 0 的 `pattern_type` 枚举已定。

### B2 — D2：Drift Analyzer 的严重性校准（依赖阶段 0 的 §3.2 schema）
- [ ] 按阶段 0 定的 `severity_calibration` schema，改写
      [drift_analyzer/agent.py](../agents/drift_analyzer/agent.py) 的 INSTRUCTION。
- [ ] **用结构化 drift 描述符替换写死的检索 hint（agent.py:74-77）** —— 这是 v2 去过拟合的核心。
- [ ] 让分析器引用检索到的 pattern 的 `support_count` / 分布作为校准依据。

### B3 — curator LLM prompt 调优（**必做**，非阻塞）
- [ ] contracts.md §3.6.4 的默认 prompt 仅为让 curator 现在就能跑通；把它调优到产品质量是
      本任务的目标，**不是可选**。
- [ ] 在保持 §3.6.3 输出 schema 不变的前提下迭代：强化“不确定就不合并”的保守默认、
      提升合并判断的准确率与 description 改写质量。
- [ ] 用 E3 的 curator 正确性实验（植入重复+垃圾）作为调优的回归标尺。
- 不阻塞 role C（C 先用默认 prompt 接线上线；B 的迭代版只要不动 schema 即可直接替换）。

### B4 — D6：Scorer 收紧（堵住 v1 宽松）
- [ ] 扩展 [memory_loop_ab_eval.py](../agents/scripts/memory_loop_ab_eval.py)：拒绝幻觉
      `source_event_ids` / 伪造时间戳；抓出嵌套在 `claim_diffs` 里的 `materiality_score`。
- [ ] 新增检查：treatment 的校准后严重性必须与 baseline **有可度量的差异**。

### B5 — 实验（Part E）
- [x] **E1** 旗舰 A/B：2026-05-30 跑通 PASS（baseline 0.75 → treatment 1.0，delta 0.25，
      negative 0.0）。产物 `agents/evals/results/memory-loop-ab-v2-2026-05-30/`。
- [ ] **E2** 累积曲线（可拍摄证据，**demo 核心镜头**）：见下方「E2 收尾计划」。
- [x] **E3** curator 正确性 + 延迟隔离：`agents/pattern_curator/scripts/_e2e_probe.py`（治理）
      + `_e3_latency_probe.py`（p95 +2.3%，隔离成立）。**role C 单人完成**。
- [x] **E4** 去写死后泛化：`agents/scripts/e4_generalization_probe.py`（检索层，rank=1）
      + `e4_e2e_setup.py`/`e4_e2e_check.py`（端到端经济学 case，7/7 过）。

---

## E2 收尾计划（2026-05-31 拍板，role C 续做）

> **核心结论**：E2 必须是「受控实验」，不能是「真实累积」。两个硬约束逼出此结论：
> 1. **可复现 ⟺ 合成**：memory loop 单调累积、不可逆——一旦 support 累到 120，
>    无法回到 support=50 的状态重录。只有「每档独立构造」才可复现可重录。
> 2. **真实累积 ≈ 0**：真集群审计 391 drift_events / 35 patterns / **0 个 outcome_switch**
>    （10,671 preprints 都没自然产出一条）。primary-outcome switch 太稀有 + 仅临床类，
>    demo 尺度内无法自累积到 50/120。

**做法：真实案例 + 受控注入。**
- **案例真实层**：用 deep-research 抓 **COMPARE 项目 / 已发表审计**里的真实 outcome-switch
  个例（试验名 / DOI / NCT 号 + 原 primary outcome + 被换成什么 + 出处），整理成可追溯案例集。
- **受控实验层**：把真实案例写成 `drift_events`（`record_source` 打标可清理），让一条
  outcome_switch pattern 的 `source_event_ids` 指向它们；按档位（5/20/50/120 或真实案例数所及）
  设定 `support_count`，每档**独立构造、跑完即清、可重录**。
- **因变量**：同一个 cardiology treatment case（始终不变）跑 drift_analyzer，记录
  `calibrated_materiality`，画 support_count → severity 收敛曲线。

**诚实话术（demo 用）**："这些是 COMPARE 审计过的**真实** outcome-switch 案例；我们**控制**
agent 可见的历史样本量（5/20/50），观察严重性校准如何随 base rate 收敛。" —— 案例真、读数真、
support 设定是「控制变量」（如同把温度计直接放进 0/50/100°C 的水测读数，而非等室温自然飘）。

**待办步骤**：
- [ ] **E2-a** deep-research 抓 COMPARE/审计的可追溯 outcome-switch 个例清单（目标数十条）。
- [ ] **E2-b** 把脚本 `e2_accumulation_curve.py` 的 source_event 从克隆占位换成真实案例；
      档位受控注入（setup/record/plot/teardown 四动作，已设计）。
- [ ] **E2-c** 各档位跑一次 treatment drift_analyzer（手工 `adk web`/`adk run`），
      `--record N <calibrated_materiality>`，`--plot` 出 ASCII 收敛曲线。
- [ ] **E2-d** 汇总 demo 可复现演示 summary（含 E1/E3/E4/E2 + 检索修复结论）。

---

## 两人交界面（均已固化进契约，几乎无需中途同步）

- **H1 — `severity_calibration` 字段 schema**：已定死于 contracts.md §3.2.2 / 其后说明。
  role C 的 ES 写 / role B 的 prompt 与 scorer 都照它。若必须改，改 contracts 后群里同步。
- **H2 — curator 内部 LLM 调用的 I/O schema 与默认 prompt**：schema 与默认 prompt 已定于
  contracts.md §3.6.3 / §3.6.4。**§3.6.4 的默认 prompt 仅为让 curator 现在就能跑通；把它
  调优到产品质量是 role B 的必做工作项（B3），不是可选。** role C 直接接线默认 prompt 先上线，
  role B 随后在 §3.6.3 输出 schema 不变的前提下迭代 prompt——schema 不变即不影响 C。

两处交界面都已写入契约，泳道 A 与泳道 B 可直接并行。

---

## 关键依赖序（避免互相等待）

```
契约已固化（contracts.md 已含 §3.2.2 / §3.6 / §2.2.5 等全部 Part C 改动）
   │
   ├── 泳道A(role C):  C1(ELSER端点/影子索引) ──► C3(curator,接 §3.6.3/§3.6.4)
   │                   C2(supervisor) 可随时并行
   │
   └── 泳道B(role B):  B1(fixture) ──► B2(severity prompt) ──► B5:E1/E2/E4
                       B4(scorer) 可随时并行
                       B3(curator prompt 调优) 需 C3 上线后才能跑通/回归 ──► 与C联合跑 B5:E3
```

- role C 应**先 C1 再 C3**（影子索引是 curator 写的落点）。
- role B 应**先 B1 再 B2**（fixture 是 severity prompt 调试的输入）。
- **E3 是唯一需要两人同时在场**的实验（验证 curator 跑动时实时检索不受影响）。
- C2 与 B4 是各自泳道里的“无依赖填充任务”，谁先空出手就先做。

---

## 完成判据（v2 收尾）

- [ ] 泳道 A：C1/C2/C3 上线，curator 增量跑通且实时检索延迟无感（E3 联合验证）。
- [ ] 泳道 B：B1/B2/B3/B4 完成，E1 显示 treatment 严重性可见优于 baseline，
      E2 累积曲线可拍摄，E4 泛化通过。
- [ ] scorer（B4）能抓住 v1 漏过的幻觉字段与嵌套 materiality_score。
