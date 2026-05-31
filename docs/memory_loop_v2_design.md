# ClaimDrift Memory Loop v2 — Design & Task Plan

Last updated: 2026-05-30
Status: design accepted, implementation not started
Owner: role C (Jiayu Zhu)

This document consolidates the decisions from the 2026-05-30 design discussion
on top of the existing [memory_loop_ab_test.md](memory_loop_ab_test.md) runbook.
It defines the next round of product/architecture changes and the experiments
that prove them.

It supersedes nothing in [contracts.md](contracts.md) yet — the contract changes
listed in **Part C** must be merged into contracts.md before implementation.

---

## 0. Why v2 (the problem with v1)

The v1 A/B test ([memory_loop_ab_test.md](memory_loop_ab_test.md)) successfully
proved the *mechanics* of the loop (`drift_event → drift_patterns →
retrieved_patterns_used → output`) but exposed three structural weaknesses:

1. **The demo task is self-contained, so memory adds no visible value.**
   "Claim disappearance" (metrics vanish at publication) can be judged from the
   two claim texts alone. Baseline (no memory) and treatment (memory) reach the
   same conclusion. Research confirms this is also *unrepresentative*: real drift
   is mostly subtle — about half of preprint→publication changes are minor and
   only ~0.5% reverse the conclusion (Brierley, PMC8806067, 3-0 verified). The
   dramatic "17.2% of COVID abstracts changed their main conclusion" claim was
   **refuted** under adversarial verification (1-2). So the v1 task is both rare
   and information-self-contained — the worst possible case for showcasing memory.

2. **The retrieval fix is overfit to the demo case.** The effective v1 change
   ([drift_analyzer/agent.py:74-77](../agents/drift_analyzer/agent.py#L74-L77))
   hard-codes an example hint ("AI diagnostic tool claim_disappearance
   quantitative performance metrics removed") whose wording mirrors the eval's
   own answer key. It works on this fixture; its generalization to other domains
   is untested.

3. **The memory write side has no governance.** `memory_synthesizer` judges
   create-vs-update at write time, locally, append-only
   ([memory_synthesizer/agent.py:103-126](../agents/memory_synthesizer/agent.py#L103-L126)).
   It structurally cannot merge stale duplicates already in the index, nor
   refresh aging `pattern_description`s. The captured
   [memory.json](../agents/evals/results/memory-loop-ab-2026-05-28/memory.json#L14-L23)
   shows the consequences: a hallucinated `source_event_id`
   (`"drift_event_id_not_found_in_input"`), a fabricated `2023-10-27` timestamp,
   and a `support_count` that does not match the real event count.

v2 addresses all three.

---

## Part A — Product direction: what memory is *for*

### A.1 The new flagship capability: historical-base-rate severity calibration

**Decision:** the primary product value of the memory loop is **calibrating the
severity / materiality of a new drift against the accumulated historical base
rate of similar drifts** — not flagging individual diffs.

Why this is the right showcase (each point is the inverse of a v1 weakness):

- **The information is not in the input.** "How extreme is this change relative
  to history?" can only come from accumulated base rates. Baseline necessarily
  cannot answer it; treatment visibly can → fixes weakness #1.
- **It improves with accumulation.** Higher `support_count` → more reliable base
  rate → sharper calibration. This is literally the memory-loop value
  proposition, and it is filmable: "fed 5 papers vs 50 papers, watch the same
  new case's severity verdict converge."
- **It has the hardest documented pain.** Outcome switching is documented with
  quantified harm: 33.4% (130/389) of trials have outcome discrepancies, and
  trials that switched their primary outcome reported effect sizes ~16% larger
  (Kim, PMC6646984, both 3-0 verified); the COMPARE project audited 13 JAMA RCTs
  and found 87 undeclared added outcomes and 70/105 pre-specified secondary
  outcomes silently dropped (3-0 verified).

**Demo task change (replaces the v1 "claim disappearance" fixture):** the new
flagship A/B fixture is a **primary-outcome switch** case where baseline can only
say "the outcome changed, severity high," and treatment says, in effect: *"In
this domain, outcome switching occurs in ~33% of trials — but here the switched
field is the* primary *outcome and it co-occurs with effect-size inflation,
placing it in the top ~5% tail of the historical distribution, so severity =
critical."*

### A.2 Second-tier capabilities (documented, but not the flagship)

These have real pain but are weaker showcases for an *accumulating* loop, so they
are backlog, not the demo centerpiece:

- **Spin-in-abstract detection.** SPIIN RCT *experimentally* showed abstract spin
  makes clinicians overrate efficacy (PMID25403215, 3-0); ~30% of abstracts carry
  spin; many clinicians read only the abstract. Strong pain, but per-paper
  detection benefits less from cross-paper memory than severity calibration does.
- **Zombie / outdated-citation alerting.** 62.6% of citation quotations
  unreliable (Gehanno, PMC9365132, 3-0); retracted papers keep being cited
  positively with long propagation lag. This is a `citation_finder` downstream
  extension, not a memory-loop showcase.
- **Cross-paper trend / repeat-offender alerting.** Conceptually attractive and
  rides on the same base-rate data, but the "repeat-offender lab/journal"
  evidence is only synthesis-level, and naming institutions carries
  product/ethics risk. Build as a *derivative* of A.1 once base rates are clean,
  not as a primary feature.

---

## Part B — Architecture changes

### B.1 `pattern_curator` — offline memory-governance job

**Form (decided):** a **standalone, independently-triggered batch job** (cron /
Cloud Run Job / Elastic Workflow trigger), built like `supervisor_agent` is — i.e.
orchestration/batch **code that is NOT an ADK `LlmAgent`**. It is deliberately
**not** a 6th Vertex reasoning engine and is **not** in the supervisor main
chain.

- It does **not** get its own conversational reasoning-engine endpoint, so it
  avoids the remote-stream + output-parse boundary that has cost us real bugs
  (event_id mismatch, MCP-wrapper-as-output, markdown-fence) —
  [supervisor_agent/agent.py:121-168, 259-287](../agents/supervisor_agent/agent.py#L121-L168).
- It **does** call an LLM internally — but only for the one step that genuinely
  needs semantics: "do pattern A and pattern B describe the same underlying
  phenomenon?" and description rewriting. "Calls an LLM" ≠ "is an agent"
  (supervisor orchestrates LLMs while not being one).

**Primary purpose (do not misframe this): purify base rates, not shrink the
index.** The flagship feature (A.1) needs trustworthy base rates. Duplicate
patterns (the same phenomenon split into A and B) corrupt the rate: a real
`support_count=20` phenomenon is miscounted as two `support_count=10` rows.
Merging duplicates *first* fixes the rate; index slimming is only a side effect.

**Responsibilities, and the code-vs-LLM boundary:**

| Sub-task | Mechanism | Scan scope |
|---|---|---|
| Find duplicates to merge | **deterministic** keyword pre-filter (`pattern_type` equal, `domain_tags` overlap) → ELSER top-k neighbor recall on the survivors → **one LLM call** to judge "same phenomenon?" | local / similarity recall, never O(N²) |
| Data hygiene (reject hallucinated `source_event_ids`, fill timestamps, recompute `support_count = len(source_event_ids)`) | **deterministic** | incremental: only patterns changed since last run (`last_updated_at` high-watermark) |
| Refresh aging `pattern_description` | **LLM** rewrite, gated by schema + "propose → validate → write" | targeted (only patterns whose evidence grew materially) |
| Evict low-quality / orphan patterns | **deterministic** filter query (`support_count < threshold`, or old + never-retrieved) | filtered query, returns small set |

Reuse the existing targeted-delete pattern from
[cleanup_probe_patterns.py](../agents/scripts/cleanup_probe_patterns.py) for the
hygiene/eviction sub-tasks rather than full-index traversal.

**Guardrails (all deterministic, wrap the LLM):**
- LLM output passes a strict schema gate before any write.
- Merge/delete are **proposals**; deterministic code validates, then writes.
  Writing to ES is always code, never the LLM.
- Conservative default: when the "same phenomenon?" judgment is uncertain,
  **do not merge** (bias errors toward keeping two rows, never toward a wrong
  merge).
- Writes use optimistic concurrency (`if_seq_no` / `if_primary_term`) so a
  curator merge cannot clobber an in-flight `memory_synthesizer` append.

**Scan policy (decided): incremental, not full.** Routine runs cost
O(new-since-last-run), not O(index size):
- duplicates → keyword pre-filter + similarity recall (local);
- hygiene → `last_updated_at` high-watermark incremental;
- eviction → filtered query.
- The only near-full pass is a **rare, human-triggered** backfill when the
  taxonomy/description spec itself changes — and even then, batched + throttled
  (see B.2).

### B.2 Dedicated ELSER inference endpoint for the curator (decided)

**Problem:** `drift_patterns.pattern_description` hard-wires
`inference_id: ".elser-2-elastic"`
([drift_patterns.json:10](../elastic/mappings/drift_patterns.json#L10)), and the
`inference_id` on a `semantic_text` field is fixed at index time — it cannot be
swapped per-query. Real-time retrieval (`search_drift_patterns`) and any curator
re-inference therefore contend on the *same* ELSER endpoint. Re-embedding many
descriptions at once can spike that endpoint and slow live retrieval.

**Decision: give the curator its own inference endpoint instance pointing at the
same ELSER model, with independent capacity/concurrency.**

Implementation shape:
- Create a second inference endpoint, e.g. `claimdrift-elser-batch`, backed by
  the same ELSER-2 model, with its own resource/concurrency allocation.
- Because `semantic_text.inference_id` is fixed at index time, the clean way to
  route curator *writes* through the batch endpoint is to write governed
  patterns into an index whose `pattern_description` binds
  `inference_id: claimdrift-elser-batch` (the shadow-index / blue-green path),
  then atomically swap via alias. Real-time retrieval reads through the alias and
  is unaffected during the rebuild.
- Real-time reads continue on the primary endpoint; as long as the curator does
  not push inference through the primary endpoint, live `search_drift_patterns`
  is isolated from curator load.
- Keep the deterministic load-reducers from the discussion anyway (keyword
  pre-filter before ELSER recall; batch + throttle; off-peak trigger) — the
  dedicated endpoint and these are complementary, not either/or.

**Does the curator disrupt normal use?** No synchronous dependency exists between
the curator and the real-time analysis chain, so it cannot block or slow
extract→drift→citation→notify directly. With a dedicated endpoint plus
batch/throttle/off-peak, the residual ELSER contention is driven to near zero at
the current demo / low-QPS scale.

### B.3 Supervisor hardening — pure code, no AI

**Decision:** the supervisor's robustness upgrade (schema validation, retry with
backoff, timeout/fallback, data hygiene, event_id consistency) is **pure
deterministic code**. These are reproducible, testable, zero-token, zero-
hallucination concerns — exactly what LLMs do worst, and exactly the class of bug
the supervisor comments already document. Keep "NOT an LLM agent —pure ADK
orchestration" ([supervisor_agent/agent.py:3](../agents/supervisor_agent/agent.py#L3)).
Do **not** turn the supervisor into an LLM.

### B.4 Agent count — explicit non-goal

We are **not** expanding toward a Sales-Shortcut-style 34-agent topology.
ClaimDrift is a depth-of-reasoning pipeline whose value concentrates in
`drift_analyzer`; splitting that into many micro-agents adds orchestration
boundaries (each a failure surface) without improving judgment. Net new
deployment units in v2: **one** (`pattern_curator`, and it is a batch job, not an
`LlmAgent`). Optional future quality agent: a `critic` that adversarially
re-checks `drift_analyzer` output — backlog, only if judgment quality needs it.

---

## Part C — Contract changes (merge into contracts.md before coding)

1. **§2.2.5 `drift_patterns`** — document the second inference endpoint
   `claimdrift-elser-batch` and the shadow-index/alias rebuild path for curator
   writes (§9.7 ELSER note currently assumes a single endpoint).
2. **§3.2 Drift Analyzer output** — add base-rate-calibrated severity fields:
   the analyzer must, for the flagship feature, emit how the current drift ranks
   against the retrieved pattern's historical distribution (e.g. a
   `severity_calibration` block: base-rate, percentile/tail position, and the
   resulting calibrated `materiality_score`). Replace the hard-coded retrieval
   hint with a structured drift descriptor (domain + drift_type + magnitude) used
   as the query, removing the overfit example.
3. **§3.5 Memory Synthesizer** — note that description refinement / dedup is now
   owned by `pattern_curator` (the existing §3.5.1 "append-evidence-only v0 TODO"
   is resolved by curator, not by changing synthesizer's write-time scope).
4. **New §3.6 `pattern_curator`** — define trigger, incremental scan policy,
   code-vs-LLM boundary, propose→validate→write guardrails, optimistic-concurrency
   writes, and the dedicated inference endpoint.
5. **`pattern_type` enum** (§2.2.5 TODO A) — confirm/extend to cover
   outcome-switch-flavored drift for the flagship fixture.

---

## Part D — Implementation tasks

Ordered; each is independently reviewable.

### D1. Demo task redesign (flagship fixture)
- [ ] Add a primary-outcome-switch A/B fixture to
      [memory_loop_ab_cases.json](../agents/evals/memory_loop_ab_cases.json):
      seed (an outcome switch), treatment (a similar switch), negative control
      (an unrelated benign change).
- [ ] Design the cases so **baseline cannot calibrate severity** (only sees the
      single diff) while **treatment can** (uses the base rate). This is the
      whole point — verify the gap is real before filming.

### D2. Severity-calibration in Drift Analyzer
- [ ] Add `severity_calibration` to §3.2 output (Part C.2) and to
      [drift_analyzer/agent.py](../agents/drift_analyzer/agent.py) INSTRUCTION.
- [ ] Replace the hard-coded retrieval hint
      ([agent.py:74-77](../agents/drift_analyzer/agent.py#L74-L77)) with a
      structured drift descriptor as the query.
- [ ] The analyzer must cite the retrieved pattern's `support_count` /
      distribution as the basis for the calibrated score.

### D3. `pattern_curator` batch job
- [ ] New standalone job (NOT an `LlmAgent`), incremental scan policy (B.1).
- [ ] Deterministic: keyword pre-filter, hygiene (reject hallucinated event ids,
      fill timestamps, recompute `support_count`), filtered eviction; reuse
      [cleanup_probe_patterns.py](../agents/scripts/cleanup_probe_patterns.py)
      patterns.
- [ ] One LLM call for "same phenomenon?" + description rewrite, behind a schema
      gate and propose→validate→write.
- [ ] Optimistic-concurrency writes (`if_seq_no` / `if_primary_term`).

### D4. Dedicated ELSER endpoint + shadow-index rebuild
- [ ] Create `claimdrift-elser-batch` inference endpoint (same ELSER-2 model).
- [ ] Shadow index `drift_patterns_v2` binding `pattern_description` to the batch
      endpoint; alias-swap rebuild path; real-time reads via alias.
- [ ] Keep keyword pre-filter + batch/throttle + off-peak trigger as
      complementary load-reducers.

### D5. Supervisor hardening (pure code)
- [ ] Schema validation + retry-with-backoff + timeout/fallback around each
      sub-agent call; no LLM logic added.

### D6. Scorer hardening (close v1 leniency)
- [ ] Extend
      [memory_loop_ab_eval.py](../agents/scripts/memory_loop_ab_eval.py) to
      reject hallucinated `source_event_ids` / fabricated timestamps and to catch
      nested `materiality_score` inside `claim_diffs` (both slipped through v1).
- [ ] Add a check that treatment's calibrated severity **differs measurably** from
      baseline's (the v1 scorer never verified memory changed the verdict).

---

## Part E — Experiment / validation plan

### E1. Flagship A/B (severity calibration) — replaces the v1 headline run
- Same baseline/seed/treatment/negative structure as
  [memory_loop_ab_test.md](memory_loop_ab_test.md), but on the D1 outcome-switch
  fixture.
- **New success criterion (the one that matters):** treatment's calibrated
  `materiality_score` / severity is *visibly different* from baseline's because
  of the base rate — not merely that a `pattern_id` appears in
  `retrieved_patterns_used`.

### E2. Accumulation curve (the filmable proof)
- Feed the loop N=5 then N=50 similar drift events; re-run the same treatment
  case; show the calibrated severity converging as `support_count` grows.
- This is the demo-video centerpiece: "memory makes it sharper the more it sees."

### E3. Curator correctness
- Seed the index with deliberate duplicate patterns (same phenomenon, two rows)
  and known-garbage rows (hallucinated event ids).
- Run curator; assert: duplicates merged (base rate corrected), garbage evicted,
  no valid pattern wrongly merged (conservative-default holds), and **real-time
  `search_drift_patterns` latency unaffected** during the run (validates B.2).

### E4. Generalization of the de-hardcoded retrieval
- Run the new structured-descriptor retrieval on a domain *outside* the fixture
  (e.g. an effect-size-reduction or hedging case) to confirm the overfit hint is
  truly gone and retrieval still finds the right pattern.

---

## Part F — Decisions log (this session)

- Flagship feature = **historical-base-rate severity calibration** (strongest
  documented pain via outcome-switching literature; best memory showcase).
- `pattern_curator` **accepted**, as an **offline batch job, not an ADK
  LlmAgent**; primary purpose is **base-rate purification**, index-slimming is a
  side effect; **incremental** scan, not full.
- Curator gets a **dedicated ELSER inference endpoint** (`claimdrift-elser-batch`)
  via shadow-index/alias rebuild, isolating live retrieval.
- Supervisor hardening = **pure code**, not AI.
- Agent-count expansion (Sales-Shortcut analogy) = **rejected** as a goal; net new
  deployment unit in v2 = one batch job.
