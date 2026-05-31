# Memory Loop v2 — 双人分工任务包（role C × role B）

Last updated: 2026-05-30
配套设计文档：[memory_loop_v2_design.md](memory_loop_v2_design.md)（决策与依据的唯一真相源）

本文件把 v2 的实现任务（设计文档 Part D/E）切成两份可并行、交界面清晰的任务包，
供 role C（架构 + GCP owner）与 role B（实验 + prompt + scorer）配合完成。

---

## 分工原则（本次拍板）

- **按专长切，不按任务序号切。** role C 扛全部基础设施与确定性代码；role B 扛全部
  实验、prompt 与 scorer。
- **AI 触点尽量归口**：curator 内部那一次 LLM 调用（“是否同一现象?” + description 改写）
  的 *骨架、护栏、以及默认 prompt* 都由 role C 一并交付——默认 prompt 已写入
  contracts.md §3.6.4（不留空位）。role B 若想调优可在其之上迭代，但不阻塞任何人。
- **契约已固化**：设计文档 Part C 的 5 处改动已经全部写入
  [contracts.md](contracts.md)（§2.2.5 双端点 + `outcome_switch` 枚举、§3.2.2
  `severity_calibration`、§3.5 治理移交说明、新增 §3.6 pattern_curator 含 §3.6.3 I/O
  schema 与 §3.6.4 默认 prompt）。两条泳道现在可直接照契约并行，无需再开“阶段 0”。

> 契约已是唯一真相源。role B 如需调整自己领域内的契约（如 `pattern_type` 枚举值、
> `severity_calibration` 字段细节），自行改 contracts.md 即可，改后在群里同步 role C。

---

## 泳道 A — role C（基础设施 / 确定性代码 / GCP / ES）

> 全部是确定性、可测试、零幻觉的工作；唯一的 LLM 触点（curator 内部判断）只搭骨架与护栏，
> prompt 文本交给 role B（见交界面 H2）。

### C1 — D4：专属 ELSER 端点 + 影子索引重建（**先做，是 C3 的前置**）
- [ ] 创建 `claimdrift-elser-batch` 推理端点（同一个 ELSER-2 模型，独立容量/并发）。
- [ ] 建影子索引 `drift_patterns_v2`，其 `pattern_description` 绑定 `inference_id:
      claimdrift-elser-batch`；实现 alias 切换的重建路径；实时读经 alias。
- [ ] 保留 keyword 预过滤 + 分批/限流 + 错峰触发作为互补降载手段。
- 产出给 role B：无（纯基础设施）。**E3 的“实时检索延迟不受影响”验证依赖本任务。**

### C2 — D5：Supervisor 加固（纯代码，不用 AI）
- [ ] 在每次子 agent 调用周围加 schema 校验 + 带 backoff 的重试 + 超时/降级。
- [ ] 维持 `supervisor_agent` “NOT an LLM agent” 现状；不加任何 LLM 逻辑。
- 与泳道 B 完全解耦，可任意时刻并行。

### C3 — D3：pattern_curator 批处理作业（依赖 C1 的影子索引就绪）
- [ ] 新建独立作业（**不是 LlmAgent**），增量扫描策略（设计文档 B.1）。
- [ ] 确定性部分：keyword 预过滤、卫生（拒绝幻觉 event id、补时间戳、重算
      `support_count = len(source_event_ids)`）、过滤式淘汰；复用
      [cleanup_probe_patterns.py](../agents/scripts/cleanup_probe_patterns.py) 的定向删除模式。
- [ ] 接好那一次 LLM 调用：输入/输出 schema 已在 contracts.md §3.6.3 定义，**默认 prompt 已在
      §3.6.4 给出（无空位）**；C 直接接线，并套上 schema 闸 + 提议→校验→写入 + 保守默认。
- [ ] 乐观并发写入（`if_seq_no` / `if_primary_term`）。
- 产出给 role B：无强制交付物（schema 与默认 prompt 已在契约内）。role B 随后必做 B3：把
  §3.6.4 默认 prompt 调优到产品质量（默认 prompt 仅保证“能跑”，不代表“够好”）。

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

### B5 — 实验（Part E；E1/E2 依赖 B1+B2，E3 依赖 C3，E4 依赖 B2）
- [ ] **E1** 旗舰 A/B：跑在 B1 的 outcome-switch fixture 上；成功标准 = 校准后严重性可见地
      不同于 baseline，而非仅出现 pattern_id。
- [ ] **E2** 累积曲线（可拍摄证据）：N=5 vs N=50，展示校准随 support_count 收敛。
- [ ] **E3** curator 正确性：植入重复+垃圾，跑 C3，断言合并/淘汰正确、无错误合并、
      且实时检索延迟不受影响（**与 role C 联合验证，因为依赖 C1**）。
- [ ] **E4** 去写死后的泛化：在 fixture 之外的领域验证 hint 确已移除、检索仍准。

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
