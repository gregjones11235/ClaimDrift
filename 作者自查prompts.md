# 作者自查（Author Self-check）输入示例与系统预期行为

适用版本：`claimdrift/selfcheck.py` + `apps/bff/review_api.py` + 前端 `/selfcheck`（2026-10-02 优化后）。
“实测”是在本地 ES（33 个案例事件）上真实跑出的结果。

作者自查有两条路径：

- **路径 1 参考文献表**：每行一条参考文献。有 DOI 的按 DOI 精确匹配（预印本 DOI 或发表版 DOI 都行）；没有 DOI 的按标题匹配（同时比对预印本标题和发表版标题）。
- **路径 2 施引句**：每行一句作者自己稿件里引用别人的句子。系统检索库里所有“旧说法”，只返回超过相关性下限的匹配，再比对句中数字，判断用的是旧值还是新值。

结果页顶部有汇总（几篇被引预印本后来被修订、几句用了旧值等），每一行都有明确状态，不会悄悄丢弃任何输入。

---

## 一、路径 1：参考文献表

结果状态：

| 状态（界面） | 含义 |
|---|---|
| Drift found（红/橙） | 库里有这篇的漂移记录，全文层面 significant/major 为红，medium 为橙 |
| Minor changes（黄） | 库里有记录，但只有 minor 级别的变化 |
| Not in library（黄） | 是 bioRxiv/medRxiv 预印本，但库里还没有；系统已预检能否现场分析 |
| Not tracked（灰） | 不是预印本，也不是库里某篇预印本的发表版；系统不追踪这类文献 |
| No match（灰） | 没有 DOI，也没有哪一篇明确对得上；有相近的会列出 “did you mean” 候选，可点开 |

| # | 情况 | 输入示例（一行一条） | 预期行为 | 实测 |
|---|---|---|---|---|
| 1 | 正确输入，库里有（预印本 DOI） | `Guan W, et al. Clinical characteristics of 2019 novel coronavirus infection in China. medRxiv 2020. doi:10.1101/2020.02.06.20020974` | **Drift found**，展示漂移摘要、两层档位和每一处变化（旧说法 → 新说法） | ✓ |
| 2 | 正确输入，库里有（发表版 DOI） | `Guan WJ, et al. Clinical Characteristics of Coronavirus Disease 2019 in China. N Engl J Med 2020;382:1708-20. https://doi.org/10.1056/NEJMoa2002032` | 同上。作者引的是发表版，提示的是它的预印本版本曾有何不同 | ✓ |
| 3 | 正确输入，库里有，变化很小 | `... medRxiv 2019. doi:10.1101/19008227` | **Minor changes**，作者自行判断是否影响自己 | 按代码 |
| 4 | 正确输入，库里有，无 DOI，写发表版标题 | `Backer JA, Klinkenberg D, Wallinga J. Incubation period of 2019 novel coronavirus (2019-nCoV) infections among travellers from Wuhan, China, 20-28 January 2020. Euro Surveill 2020;25(5).` | **Drift found**，标注 “matched by title”，并显示匹配到的论文标题 | ✓（标题词覆盖率 100%） |
| 5 | 正确输入，库里有，无 DOI，标题写得不全 | `Backer JA, et al. The incubation period of 2019-nCoV infections among travellers from Wuhan. medRxiv 2020.` | 覆盖率 ≥ 85%：直接匹配；60%–85%：匹配并标 “title match — please confirm”，请作者确认是否同一篇 | ✓（覆盖率 87.5%，直接匹配） |
| 5a | 简化/截断输入 | `Guan W, et al. Clinical characteristics of 201`；`Guan, Clinical characteristics`；`Backer incubation period travellers` | 输入的词（含第一作者姓、被截断的最后一个词）全部属于某篇论文，且至少 2 个是标题词：匹配并标 “title match — please confirm” | ✓ 三条都匹配到正确论文（Guan / Backer），均标“请确认” |
| 5b | 简化输入，对不上任何一篇 | `Clinical characteristics of COVID-19` | **No match**，下面列出 “did you mean one of these?” 最多 3 个候选，点开可看完整变化 | ✓ 候选含 Guan 和肝损伤那篇 |
| 6 | 正确输入，库里没有，预印本已发表且全文开放 | `Gentile JE et al. Evidence that minocycline treatment confounds the interpretation of neurofilament as a biomarker. medRxiv 2024. doi:10.1101/2024.05.01.24306384` | **Not in library**，预检显示 “Can be analysed now (published as …; usually takes 2–4 minutes)”，出现 **Analyse now** 按钮。点击后后台跑第①层（论断抽取 ×2 → 漂移分析），状态存进 ES（服务重启不丢），完成后可打开事件 | 预检 ✓（ready）；分析流程已在 Playground 验证 |
| 7 | 正确输入，库里没有，尚未正式发表 | 任一还没有发表版的 bioRxiv DOI | **Not in library**，预检显示 “No published version yet — nothing to compare against”，**不显示**分析按钮 | 按代码 |
| 8 | 正确输入，库里没有，发表版全文不开放 | 发表版不在 Europe PMC 开放全文中的预印本 | 预检显示 “The published version has no open full text — cannot be analysed”，不显示分析按钮 | 按代码 |
| 9 | 正确输入，普通期刊论文 | `Jones B. A journal paper not tracked. Nature 2021. doi:10.1038/s41586-021-00000-0` | **Not tracked**，说明系统只追踪预印本到发表版的变化（不等于“确认没有问题”） | ✓ |
| 10 | 正确输入，无 DOI，库里没有 | `Polack FP, et al. Safety and efficacy of the BNT162b2 mRNA Covid-19 vaccine. N Engl J Med 2020.` | **No match** | ✓（最高覆盖率 33%） |
| 11 | 胡乱输入，DOI 格式对但不存在 | `doi:10.1101/2023.01.01.522222` | **Not in library**，预检显示 “DOI not found on bioRxiv/medRxiv — check the DOI”，不显示分析按钮 | ✓ |
| 12 | 胡乱输入，长 | `lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod` | **No match** | 按代码 |
| 13 | 胡乱输入，短 | `asdf qwer zxcv` | **No match**，无候选 | ✓ |
| 13a | 极短输入，只有作者姓 | `Guan 2020`、`Backer` | 不设长度下限，照常匹配：**No match**，但按作者姓列出候选（Guan / Backer 那篇），点开可看变化 | ✓ |
| 14 | 空输入 | （空白） | 按钮不可点；直接调接口返回 400 `no_text` | 按代码 |
| 15 | 超长输入 | 超过 10 万字符 | 只查前 10 万字符，汇总栏红字提示截断 | 按代码 |

预检最多对 30 个不在库里的 DOI 并行进行（只查 bioRxiv API 和 Europe PMC，不调用模型）。

---

## 二、路径 2：施引句

每条匹配有一个强度：

- **close match**：ELSER ≥ 25 且 BM25 ≥ 20；
- **possible match**：ELSER ≥ 23 或 BM25 ≥ 12；
- 低于下限的不算匹配，默认隐藏，可以点 “Show the nearest candidates” 查看。

下限在 67 句真实施引句和 35 句无关句上标定：

- 真实句只有 3/67 落在下限之下；
- 无关句有 34/35 落在下限之下。唯一超过下限的是一句 SARS-CoV-2 表面稳定性的句子，库里确实有同主题的变化。

数值判定：

| 判定 | 含义 |
|---|---|
| `uses_old_value` | 用了旧值。附 “suggested fix”：正式版原文加发表版 DOI，可一键复制 |
| `mentions_both` | 新旧值都提到了，同样附 suggested fix |
| `uses_current_value` | 用的是新值 |
| `unchanged_value` | 句中数字在新旧两版都有，没变 |
| `cannot_tell` | 句中没有可比的数字（不猜） |

| # | 情况 | 输入示例 | 预期行为 | 实测 |
|---|---|---|---|---|
| 1 | 库里有，用了旧值 | `The mean incubation period was 5.8 days among travellers from Wuhan (Backer et al.).` | 第 1 条为 Backer，close match，**Uses superseded value**，附正式版 6.4 天原文和复制按钮 | ✓ close match（ELSER 38.2 / BM25 33.6） |
| 2 | 库里有，用了新值 | `The mean incubation period was estimated at 6.4 days (Backer et al.).` | 第 1 条为 Backer，**Uses current value** | ✓（优化前已验证） |
| 3 | 库里有，没有数字 | `Incubation period estimates for travellers from Wuhan have been reported (Backer et al.).` | 第 1 条为 Backer，**Cannot tell**，作者自己对照新旧说法 | ✓（优化前已验证） |
| 4 | 库里没有（相关领域） | `Remdesivir shortened the time to recovery in adults hospitalized with Covid-19 (Beigel et al.).` | **No match**：“No drift event in the library matches this sentence” | ✓ |
| 5 | 胡乱输入（像句子） | `The weather in Paris was pleasant and the croissants were excellent.` | **No match** | ✓ |
| 6 | 胡乱输入（乱码） | `asdf qwer zxcv 123` | **No match** | 按标定数据（ELSER 3.9 / BM25 0） |
| 7 | 空输入 | （空白） | 按钮不可点；接口返回 400 `no_sentences` | 按代码 |
| 8 | 超过 20 句 | 25 行 | 前端提示 “too many” 并禁用按钮；直接调接口时只查前 20 句，汇总栏提示其余未查 | 按代码 |
| 9 | 单句过长 | 一句 3000 字符 | 只检索前 2000 字符，该句上方红字提示 | 按代码 |
| 10 | 引用了被人工驳回的事件 | 驳回某事件后再查它的施引句 | 驳回的事件已从自查索引删除，不出现 | 按代码 |

检索模式下拉框：`hybrid`（默认）、`elser`（纯语义）、`bm25`（纯关键词）。单一模式下只用该模式自己的分数判断强度。

---

## 三、仍未覆盖的（下一步，第 6 项）

上传整篇稿件后，系统自动拆出参考文献表和施引句，并按引用标号把每句话对应到它引用的那篇文献，只比对那一篇的变化。这一项目前还没有做。

---

## 四、路径 3：已发表论文（2026-10-02 新增）

作者输入自己**已经发表**的论文的 DOI 或 PMCID。系统通过 MCP 工具读这篇论文（与引用分析任务用的是同一套工具：`get_reference_list`、`get_citation_sentences`、`search_in_work`、`verify_citing_quote`；工具无状态，每次调用都带上目标字段），从参考文献表里找出库内后来被修订的预印本，用引用分析子 agent 的同一套提示词判定它是否沿用了旧值。

| # | 情况 | 输入示例 | 预期行为 | 实测 |
|---|---|---|---|---|
| 1 | 已发表、全文开放，引用了库内被修订的预印本 | `PMC7097845` 或 `10.1016/S2214-109X(20)30074-7`（Hellewell 等，Lancet Glob Health 2020） | 列出它引用的库内预印本；Backer 一条判 **superseded**，用途为 model_input（把 5.8 天当模型参数），施引句逐字核验通过；R0 那篇判 not_relying | ✓ 约 16 秒；R0 那条的用途在不同运行间是 background 或 unknown（模型判定有波动） |
| 2 | 已发表，但没引用库内任何预印本 | 任一无关论文的 PMCID | “Your paper cites none of the revised preprints in the library.” | 按代码 |
| 3 | 引用了库内预印本，但那次修订没有可追踪的具体数值 | — | 该条显示 “this preprint is cited, but its revision has no specific value to trace” | 按代码 |
| 4 | Europe PMC 没有开放全文 | 闭源期刊论文的 DOI | “Europe PMC has no open full text for this paper” | 按代码 |
| 5 | 查不到 | 不存在的 DOI | “This paper was not found in Europe PMC.” | 按代码 |
| 6 | 格式不对 | `hello` | 400：要求输入 DOI、PMCID 或 PPR 编号 | 按代码 |

每篇论文最多判定 8 个被修订的数值，4 个并行。
