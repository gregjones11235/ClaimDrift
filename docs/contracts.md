# ClaimDrift — Inter-component Contracts

> **Deployment**: five ADK agents on Vertex AI Agent Engine; the MCP tool service, BFF, Playground, frontend,
> dispatcher and citation job on Cloud Run; Elasticsearch Serverless.
> **Scope**: cross-component contracts only — what one component may assume about another. Prompts, UI components and
> puller internals live with their code.

---

## About this document

**It is the single reference for anyone changing a component boundary.** Team members develop in parallel, each with
their own coding agent (Claude Code, separate accounts); every one of those agents works against this document, not
against another member's code. Agent payloads, ES fields and enums, API and SSE shapes, and the change process are
fixed here before code is written, so separately developed parts fit together.

- **Authority.** Where this document and code disagree, the code listed in each section is authoritative and this
  document is fixed in the same change. ES mappings: `elastic/mappings/*.json`. Frontend types:
  `frontend/src/types/claimdrift.ts`.

---

## 0. Conventions

- **Names**: snake_case fields, lowercase_underscore enums, plural index names, agent ids `claim_extractor` etc.
- **Time**: ISO 8601 UTC. Every `*_at` field is set by program code (`store.now()`), never by a model.
- **DOI**: bare, lowercase, no `https://doi.org/` prefix.
- **IDs**: natural or deterministic keys wherever one exists (§4.1); random UUIDs only for queue items (`run_id`).
- **Nullable**: explicit `null`, not empty strings.
- **Model output never reaches ES unfiltered**: every mapping is `dynamic: strict`, and model output is copied through
  explicit whitelists (`claimdrift/store.py`), so an invented key is dropped instead of failing the write.

---

## 1. System skeleton

### 1.1 Layers

| Layer | What | Where |
|---|---|---|
| ① drift analysis | preprint v1 vs published full text (JATS) → one drift event | supervisor + claim_extractor + drift_analyzer |
| ② citation analysis | which citing papers still use a superseded value | citation_finder, run as queued jobs |
| ③ author self-check | a sentence or a published paper against the library of revised claims | BFF (`claimdrift/selfcheck.py`) |
| notification | one notice per citing paper that relies on a revised value | notifier, called after a citation run |

### 1.2 The five agents

Each is an ADK `BaseAgent` on Vertex AI Agent Engine (`agents/<name>/agent.py`), a thin wrapper around one handler in
`claimdrift/agent_handlers.py`. Locally the same handlers run in-process. Models: `CLAIMDRIFT_MODEL_PRO` /
`CLAIMDRIFT_MODEL_FLASH` (`claimdrift/config.py`); Gemini through the AI Studio API key.

| Agent | Model | Role | Autonomous loop |
|---|---|---|---|
| `supervisor` | none (fixed code) | Layer ① in a fixed order; quote verification and review triggers; in the Playground also ② and the notices | No: deterministic workflow agent |
| `claim_extractor` | flash | Claims (findings + method/definition statements) of one version, section chunks in parallel | No |
| `drift_analyzer` | pro | Compares the two claim lists: drifts, root cause, materiality, full-text tier; abstract-level class alongside | No: one call + one JSON repair turn |
| `citation_finder` | pro (+ flash for the quantity) | Orchestrator + workers over the MCP tools; one step per call | **Yes** |
| `notifier` | flash | Drafts one notice; template fallback | No |

### 1.3 Non-agent components

| Component | Runs on | Role |
|---|---|---|
| pullers | Cloud Run Jobs + Scheduler | bioRxiv / medRxiv / Crossref → `preprints` |
| Elastic Workflow `dispatch_new_pairs` | Elastic, every 5 min | new (preprint, published) pairs → dispatcher `/dispatch` |
| `claimdrift-dispatcher` | Cloud Run | `/dispatch` → Pub/Sub → `/run` → layer ① (§6) |
| `claimdrift-mcp` | Cloud Run | MCP tool service: the agents' only access to full texts and Europe PMC (§3) |
| `claimdrift-citation-dispatch` | Cloud Run Job | drains `citation_runs`, then notifies (§4.4) |
| `claimdrift-bff` | Cloud Run | read API, review API, self-check, auth (§7) |
| `claimdrift-playground` | Cloud Run | live 5-agent run over SSE (§7.3) |
| `claimdrift-frontend` | Cloud Run | Next.js dashboard |

### 1.4 Data flow

```
preprints ──(workflow, 5 min)──► dispatcher /dispatch ──► Pub/Sub ──► /run
                                                                        │ pipeline.analyze_dois
                                                                        ▼
            supervisor: claim_extractor x2 ─► drift_analyzer ─► verify_quote (MCP) ─► review triggers
                                                                        │ computed {event, targets, analysis}
                                                                        ▼
                         persist (caller side): drift_events, claims, drift_claims_search, citation_runs (queued)
                                                                        │
                    citation job: citation_finder start/step ... ─► affected_citations ─► notifier ─► notification_log
```

### 1.5 Design rules every component relies on

1. **Agents only compute** (部署方案 A7). No agent writes ES, sends mail or writes a cache. Side effects run on the
   caller side: `pipeline.persist()` (layer ①), `citations/jobs.py` (layer ②), `notifier.notify()` (notices).
   Consequence: a failed agent call is safe to retry (§2.1).
2. **Mechanical fields come from code, never from a model**: `event_id`, DOIs, titles, authors, versions, every
   timestamp, `target_id`, `run_id`, document ids.
3. **The MCP service is the only window on paper text and Europe PMC.** It is stateless: every call carries the target
   fields; callers inject them (`McpClient.bind`), models never see them.
4. **Human decisions are never overwritten.** Re-analysis keeps `approved` / `rejected` visible
   (`reanalyzed_after_review`); a citation run skips papers a human already decided.
5. **Coverage is always reported.** A truncated citation run must never look complete (`coverage_complete`,
   `coverage_notes`).
6. **Prompts are versioned and evaluation-locked.** `claimdrift/prompts/drift_analyzer.py` (`PROMPT_VERSION = "v4a"`)
   changes only together with a re-run of the 33-case evaluation.

---

## 2. Agent interface

### 2.1 Wire protocol (all five)

Request: the JSON payload as the user message (`stream_query(message=json.dumps(payload))`). Response: a stream of ADK
events, each with one JSON text part (`agents/common.py`, `claimdrift/engines.py`):

```json
{"cd": "progress", "data": {...}}     // any number
{"cd": "result",   "data": {...}}     // exactly one on success
{"cd": "error",    "error": "..."}    // on failure; errors are in-band, never an opaque HTTP 500
```

Caller rule (`engines.call_remote`): an exception, a `cd: error`, or a stream without `result` is retried, 3 attempts,
waits 10 s / 20 s. Sessions are in memory and thrown away per request; Agent Engine Sessions / Memory Bank are not used.

### 2.2 Payloads

| Agent | Request | Result |
|---|---|---|
| `claim_extractor` | `{doc_id, doc}` | `{claims, usage}` |
| `drift_analyzer` | `{pair, claims, extract_usage, force_route}` | `{analysis, abstract_severity}` |
| `citation_finder` | `{op: "start", target, since, exclude}` or `{op: "step", state}` | `{state}`; `state.phase == "done"` ⇒ `state.result` |
| `notifier` | `{ac, event}` (an `affected_citations` doc + its drift event) | `{subject, body}` |
| `supervisor` | `{mode: "analyze" \| "full", pair, force_route, max_targets, max_alerts}` | `{computed}`; mode `full` adds `{citation, drafts}` |

`pair` / `doc` are `claimdrift/serial.py` dicts (JATS parsed into abstract, sections, tables). Locally (no engines) the
handlers run in-process with identical payloads.

**claim** (`claim_extractor.py`):

```json
{"id": "pre0", "doc_id": "preprint_v1", "section": "Results", "text": "<verbatim sentence>",
 "kind": "finding | method_definition", "numbers": [{"metric": "...", "value": "...", "unit": "..."}],
 "support_sections": ["<outline title>"]}
```

**analysis.output** (drift_analyzer, after `normalize`; schema text in `claimdrift/prompts/drift_analyzer.py`):

```json
{"drift_summary": "1-3 sentences",
 "claim_diffs": [{"diff_type": "...", "preprint_text": "...", "published_text": "...", "change_description": "...",
                  "root_cause": "...", "materiality": 0.72, "severity_tier": "significant",
                  "evidence": [{"doc_id": "preprint_v1 | published", "section": "...", "quote": "<verbatim>"}],
                  "rationale": "...", "label_problems": ["..."]}],
 "materiality_score": 0.72, "fulltext_tier": "significant"}
```

`severity_tier` / `fulltext_tier` are computed by code from `materiality` (`tier_of`: <0.3 minor, <0.6 medium, <0.9
significant, else major; the event tier is the highest diff tier). Labels outside the closed sets are kept and listed in
`label_problems`, never silently fixed. `analysis.output = null` (no parseable JSON after the repair turn) makes the
supervisor fail.

**abstract_severity** (`abstract_severity.py`, Brierley et al. 2022 scale, 12 fixed human-labelled exemplars):
`{class: no_change | minor | major | null, changes: [{section, what, degree}], scale: "brierley_2022", exemplar_set, model}`.

**citation target** (`citations/targets.py`; one per claim_diff with a superseded value, root causes `wording_only` /
`reporting_choice` skipped, diffs without a number produce none):

```json
{"target_id": "<event_id>::<claim_diff_idx>", "drift_event_id": "...", "claim_diff_idx": 0, "paper_id": "...",
 "preprint_doi": "...", "published_doi": "...", "first_author": "...", "title_fragment": "...",
 "drift": {"id": "D0", "preprint_v1_claim": "...", "current_claim": "..."},
 "terms": ["5.8 days"], "target_source": "derived"}
```

`terms` = old values derived by `terms.old_value_terms` (program, no model). The orchestra adds `quantity`
(`{property_terms, unit}`, one flash call).

**citation result** (`state.result`, `orchestra.Orchestra.result`): `citing_works[]` (one per judged paper: `work_id`,
bibliographic fields, `cites`, `role`, `sentence`, `sentence_verified`, `relayed_by`, `unclear_rule`, `reason`, `flags`,
`found_via`, `judged_by`, `verification`, `verdict_before_verification`), plus `n_candidates`, `n_judged`, `n_unjudged`,
`unjudged[]`, `coverage`, `coverage_notes`, `coverage_complete`, `calls`, `tokens`, `events`, `summary`, `terms`.
Run limits (`config.py`): orchestrator 14 turns, dispatch ≤ 3 rounds × 8 groups × 5 papers, ≤ 2 chain hops, 120 papers
sent to workers per run, a worker's tool budget = 3 × its papers, pre-screen ≤ 2000 papers.

---

## 3. MCP tool service (`claimdrift/mcp_server.py`)

Stateless streamable HTTP on Cloud Run (`/mcp`, Google ID token); stdio locally. Full texts are downloaded, parsed and
cached only here.

| Group | Tools | Used by |
|---|---|---|
| paper full text | `list_documents`, `list_sections`, `read_section`, `search_text`, `get_table` (`paper_id`, `doc_id`) | available to agents; the current pipeline does not call them |
| verification | `verify_quote(paper_id, doc_id, quote)` → `{verified, section, offset, text_source}` | supervisor (provenance) |
| citing literature | `get_citation_sentences(work_id, first_author, preprint_doi, published_doi, title_fragment)`, `search_in_work(work_id, terms, ...)`, `verify_citing_quote(s)`, `citation_evidence`, `get_reference_list` | citation workers, verifier, self-check |
| candidate search | `list_citers` + `screen_citers` (pre-screen in batches), `prescreen_citers`, `search_more_citers`, `follow_citation_chain` | citation orchestra |

`work_id` = the citing paper's Europe PMC id (PMCID, or the PPR id of a preprint). The target fields identify the
cited preprint; callers bind them. Worker-visible tools: `get_citation_sentences(work_id)`,
`search_in_work(work_id, terms)` only.

---

## 4. Elasticsearch indices

### 4.1 Overview

| Index | `_id` | Written by | Read by |
|---|---|---|---|
| `preprints` | `{doi}::{version}` | pullers | workflow, dispatcher |
| `drift_events` | `uuid5(preprint_doi, published_doi)` (`store.event_id_for`) | `pipeline.persist`; `citations/jobs` (`citation_analysis`); BFF review | BFF, frontend, notifier, self-check |
| `claims` | `{event_id}::{claim id}` | `pipeline.persist` | BFF |
| `drift_claims_search` | one doc per claim_diff | `pipeline.persist` (`selfcheck.index_event`) | BFF self-check (ELSER + BM25) |
| `citation_runs` | `run_id` (uuid4) | `pipeline.persist` (queue), `citations/jobs` (state, result) | citation job, BFF |
| `affected_citations` | `{event_id}::{work_id}` | `citations/jobs`; BFF review | notifier, BFF, frontend |
| `notification_log` | `affected_citation_id` | `notifier.notify` | BFF, frontend |
| `dispatch_state` | `main_flow` (watermark), `dispatch_budget` | workflow; dispatcher | workflow, dispatcher |
| `auth_users`, `auth_sessions` | — | `claimdrift/auth.py` | BFF |
| `agent_events` | — | no current component writes it | BFF `/api/events/stream` |

Deterministic `_id`s make re-processing idempotent at the document level: re-analysing a pair overwrites its event,
re-judging a paper overwrites its citation doc. Queue items (`citation_runs`) are not deduplicated.

### 4.2 `drift_events` (field contract)

Built by `store.drift_event_doc` inside `pipeline.compute` (no side effects; this is what the supervisor runs), then
completed and written by `pipeline.persist` on the caller side.

| Field | Set by | Meaning |
|---|---|---|
| `event_id`, `preprint_doi`, `published_doi`, `paper_id`, `preprint_title`, `published_title`, `first_author`, `preprint_versions`, `preprint_withdrawn_versions`, `preprint_version_compared` (`v1`), `text_source` (`jats`), `detected_at`, `analyzed_at`, `record_source` (`pipeline` \| `case_bank`) | code | identity and provenance of the comparison |
| `drift_summary`, `claim_diffs[]`, `materiality_score` | drift_analyzer (whitelisted) | the analysis; `claim_diffs[].evidence` stored unindexed |
| `fulltext_severity` `{tier, materiality_score, prompt_version, model}` | code from drift_analyzer output | full-text dimension |
| `abstract_severity` | drift_analyzer (abstract call) | abstract dimension; never merged with the full-text one |
| `provenance[]` `{claim_diff_idx, doc_id, doi, version, section, section_claimed, quote, offset, tool, text_source, verified, verified_at}` | code (`provenance.check` via MCP `verify_quote`) | one row per evidence quote |
| `verification` `{quotes_total, quotes_verified, status, verified_at, method}` | code | `verified` all found / `partial` / `unsupported` none or no quotes |
| `analysis_route` `{route, est_tokens, limit, forced, prompt_version}`, `analysis_mode`, `usage` | code | how the event was produced |
| `review_status`, `review_reasons` | code (`review.apply_event`), then humans | §5 |
| `reviewer`, `reviewed_at`, `review_note`, `corrected_fields`, `machine_verdict` | BFF review | human decision; `machine_verdict` keeps the overridden machine values |
| `citation_analysis` `{status, n_targets, run_ids, checked_until, n_superseded, coverage_complete, updated_at}` | `persist`, then `citations/jobs` | layer ② summary |

Mapped but not written by the current code: `retrieved_patterns_used`, `severity_calibration`,
`claim_diffs[].numerical_delta`, `tool_trace`; `claim_diffs[].preprint_claim_id` / `published_claim_id` are written as
`null`.

### 4.3 `affected_citations`

One doc per judged citing paper and target (all classes, so negatives stay auditable), built by
`store.affected_citation_doc` from `citing_works[]`: `target_id`, `claim_diff_idx`, `run_id`, `work_id`, citing-paper
bibliography (`citing_paper_doi/title/authors/date/journal`; author e-mail is never extracted), `cites`, `role`,
`sentence`, `sentence_verified`, `found_via` + `found_via_detail`, `relayed_by`, `flags`, `unclear_rule`, `reason`,
`judged_by`, `verification`, `verdict_before_verification`, `needs_notification` (= `cites` ∈ {superseded, indirect}),
`notify_priority` (`high` superseded + model_input, `normal` other superseded, `low` indirect), review fields.
Mapped but not written by the current code: `severity_tier`, `severity_reasoning`.

### 4.4 `citation_runs` (queue and record)

`status`: `queued` → `running` (claimed with optimistic concurrency on `_seq_no`) → `done` | `failed`. `kind`:
`initial` (queued by `persist`) | `incremental` (`rerun_due`, only papers first published since `checked_until`).
While running, `state` (the full orchestra state) and `progress` are saved after every step; a run with no step for
30 minutes is claimed again and resumes from its last state. On `done`, `state` is cleared and the result fields are
copied in (`n_*`, `coverage*`, `unjudged`, `judged_by`, `counts`, `calls`, `tokens`, `events`, `prefetch`, `summary`,
`terms`, `checked_until`). On `failed`, the last state stays for inspection with `error`.

### 4.5 `notification_log`

`recipient_email` is always `NOTIFY_OVERRIDE_EMAIL` (the project test inbox); `intended_for` records the paper and
authors the notice is about. `status`: `drafted` | `sent` | `failed`; `delivery`: `gmail` | `draft_only`.
`reasoning_trace` = the citation verdict's `reason`. One notice per `affected_citation_id`; an existing row is never
re-sent.

---

## 5. Enums

| Name | Values | Defined in |
|---|---|---|
| `diff_type` | `claim_disappeared`, `claim_added`, `numerical_shift`, `hedging_added`, `hedging_removed`, `claim_reversed`, `outcome_switch` | `drift_analyzer.DIFF_TYPES` |
| `root_cause` | `definition_change`, `analysis_removed`, `reporting_choice`, `model_respecified`, `outcome_redefinition`, `method_change`, `generality_reversed`, `data_revision`, `wording_only` | `prompts/drift_analyzer.ROOT_CAUSES` |
| full-text tier | `minor` (0–0.3), `medium` (0.3–0.6), `significant` (0.6–0.9), `major` (0.9–1) | `prompts/drift_analyzer.TIERS` |
| abstract class | `no_change`, `minor`, `major` | `abstract_severity.CLASSES` |
| `verification.status` | `verified`, `partial`, `unsupported` | `provenance.py` |
| `review_status` | `not_required`, `pending`, `approved`, `rejected` | `review.py` |
| event `review_reasons` | `quote_unverified`, `severity_mismatch`, `withdrawn_version`, `reanalyzed_after_review` | `review.py`, `pipeline.py` |
| citation `review_reasons` | `unclear` | `review.citation_status` |
| `cites` | `superseded`, `current`, `flagged_as_previous`, `indirect`, `not_relying`, `unclear` | `citations/orchestra.VERDICTS` |
| `role` | `model_input`, `reported_as_fact`, `background`, `unknown` | `citations/orchestra.ROLES` |
| `flags` | `F1` old value not next to a sentence citing the target, `F2` value only in a table row / list, `F3` old and current value in one sentence | `citations/orchestra.py` |
| `unclear_rule` | `F1`, `F2`, `F3`, `M1`, `M2` | `UNCLEAR_RULES` |
| `found_via` | `prefetch`, `search_more`, `chain` (`semantic` reserved) | `store._found_via` |
| `citation_analysis.status` | `queued`, `no_target`, `not_queued`, `running`, `done`, `failed` | `pipeline.persist`, `jobs._update_event` |

Review: an event goes to `pending` when any trigger holds (an evidence quote failed the verbatim check; abstract and
full-text severity clearly contradict; the preprint has a withdrawn version). Humans may correct only
`abstract_class`, `fulltext_tier` and per-diff `root_cause` / `severity_tier` (events), `cites` / `role` (citations).
Notifications are not gated by review (decision 2026-10-01); `rejected` items are never notified.

---

## 6. Dispatcher (`apps/dispatcher/main.py`)

| Endpoint | Auth | Contract |
|---|---|---|
| `POST /dispatch` `{preprint_doi, published_doi}` (`?force=true`) | static bearer (workflow) | budget check → idempotency gate (a `drift_events` doc for the DOI pair exists ⇒ `already_processed`) → pre-check (v1 JATS + open published full text) → publish to Pub/Sub → `enqueued`. Other answers: `paused_budget_exhausted`, `not_analysable`. Always 2xx unless the publish fails (so the workflow watermark never skips a pair) |
| `POST /run` | Pub/Sub push, Google OIDC (`aud` + service account) | same idempotency gate, then `pipeline.analyze_dois`; answers `completed`, `already_processed`, `aborted_no_v1`, `skipped_not_analysable`, `failed`, `dropped_invalid` — all 2xx so Pub/Sub does not redeliver a deterministic failure |
| `GET /health` | none | budget state |

Budget: `dispatch_state/dispatch_budget` holds `budget_max` / `budget_used` / `analyzed_pairs` (counted once per pair).
When used up, the dispatcher pauses the puller schedules and disables the workflow itself.

The idempotency gate is check-then-act: a redelivery that arrives while the first run is still in progress (no event
written yet) is not caught. The event itself is still written once (deterministic `_id`); the duplicate cost is a second
analysis and a second set of queued citation runs.

---

## 7. BFF and Playground

### 7.1 BFF HTTP API (`apps/bff/server.py`, `apps/bff/review_api.py`)

Session cookie `cd_session`, forwarded by the frontend. Public: `GET /api/health`, `GET /api/stats` (pending-review count
admin-only), `POST /api/auth/{login,register,logout}`. Admin only: `/api/review-queue`, `/api/review/*`. Everything
else needs a login.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/auth/me` | current user |
| GET | `/api/drift-events` | `{items (most recent 100), count (index total)}` |
| GET | `/api/drift-events/<id>` | the `drift_events` doc |
| GET | `/api/drift-events/<id>/{claims,affected-citations,notifications,citation-runs}` | `{items, count}` |
| GET | `/api/review-queue?kind=all\|events\|citations&status=pending` | review queue |
| GET | `/api/review/{events,citations}/<id>` | review detail |
| POST | `/api/review/{events,citations}/<id>` `{decision, note, corrected}` | records a human decision (§5) |
| POST | `/api/selfcheck/{sentences,references,published,analyze}` | author self-check; `GET /api/selfcheck/analyze?doi=` polls an on-demand analysis |
| GET | `/api/events/stream` | SSE agent timeline from `agent_events` |

`BFF_SEED_DATA=1` serves `elastic/demo_seed/` without ES; review and self-check then answer 503.

### 7.2 Frontend types

`frontend/src/types/claimdrift.ts` is the frontend's copy of §4–§5. Change it in the same commit as the field or enum
it mirrors.

### 7.3 Playground SSE (`apps/playground/orchestration.py`)

`GET /api/playground/orchestrate?email=<address>` runs the supervisor in mode `full` on one case-bank pair and relays
its frames. Nothing is written to ES; drafts are mailed to the address typed on the page (≤ `PLAYGROUND_MAX_EMAILS`),
one run at a time.

Frames (`event: <name>`, `data: <json>`): `run.started`, `pipeline.warming`, `pipeline.ready`, `node.started` /
`node.active` / `node.output` / `node.done` / `node.error` (`{node, lane, action | summary | message}`, `node` ∈
`claim_extractor`, `drift_analyzer`, `citation_finder`, `notifier`), `drift.minted` (`{event_id, drift_summary}`),
`email.sending` / `email.sent` / `email.failed`, `run.error`, `run.complete` (`{event_id, emails_sent, summary, secs}`).

---

## 8. Change rules

| Change | Process |
|---|---|
| Add a field | Mapping JSON (`elastic/mappings/`) + live index + `store.py` whitelist + frontend type + this document, in one change |
| Rename a field / change a type / change an enum | Same, plus the readers listed in §4.1; enum values are closed sets checked in code (`label_problems`, review validation) |
| Change a prompt | Bump its version; re-run the evaluation it is locked to (drift_analyzer: 33 cases) before deploying |
| Change an agent's payload | Update §2.2 and redeploy the agent **and** its callers (`claimdrift/scripts/deploy_agents.py`; the BFF / Playground images copy `agents/engines.json`) |
| Change agent order or responsibility | Architecture change: §1 and §2 first, then code |

Deployed agents run the code packaged at deploy time: a local change is not live until the engine is redeployed.
