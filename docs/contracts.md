# ClaimDrift — Inter-component Contracts

> **Status**: Phase 4 complete (memory loop live on Vertex AI Agent Engine via Elastic MCP); see Changelog for current state.
> **Scope**: Cross-component contracts only. Component-internal details (specific prompts, frontend components, puller implementations) live in their own repos / files, not here.

---

## About this document

**Two things to know before reading**:

1. **This document is both human-readable and machine-usable**. The prose parts (responsibility descriptions, design decisions, field semantics) help the team align on shared understanding. The code blocks (JSON schemas, ES mappings, SSE event formats) are the source of truth — they're meant to be copy-pasted directly into Agent Builder tool definitions, ES index mappings, and the frontend's TypeScript types. So keep code-block field names and types strictly accurate; descriptive paragraphs can be looser.

2. **This document only covers cross-component interfaces, not total workload**. The bulk of each person's work lives in their own repo / tool (C in Agent Builder + Cloud Run; B in puller implementations + ES config; A in Google AI Studio writing prompts; D in the frontend repo). TODO counts in this document do not reflect workload distribution — see "Team allocation at a glance" below for that.

---

## Team allocation at a glance

Four-person team. This document only covers cross-component interfaces; the real bulk of each member's work lives in their own repos / tools.

**A (prompt engineering + biomedical domain)**:  //Taiyang

- Prompts for the 5 Gemini agents (iterated in Google AI Studio, checked into `prompts/`)
- Manual ground-truth annotation for 5–10 demo-grade drift cases
- Reference examples for Notifier email tone calibration
- Domain content for video / Devpost copy

**B (full-stack engineer, data + backend)**:  //Jeremy

- The 4 Python pullers of the ingestion pipeline (Cloud Run Jobs + Cloud Scheduler)
- Full mappings for the 6 Elasticsearch indices (the TODO B items in §2)
- Elastic Ingest Pipeline + ELSER configuration
- Frontend BFF + Server-Sent Events channel
- Notifier email dispatch code

**C (architecture + GCP owner)**:  //Jiayu Zhu (Alec)

- Actually building the 5 agents in Google Cloud Agent Builder + tool wiring
- Elastic MCP server configuration (so Agent Builder can call ES|QL)
- Actual orchestration in Elastic Workflows
- **Memory loop implementation and tuning** (the project's biggest selling point)
- Cloud Run deployment + overall E2E debugging

**D (frontend + demo video + external packaging)**:  //tty (Ranjan)

- The entire Next.js + Tailwind + shadcn/ui frontend, deployed to Vercel (6 views)
- 3-minute demo video (script, screen recording, voiceover, edit)
- Devpost submission copy + README packaging
- Agent Builder configuration runbook (insurance against API/quota changes since Agent Builder is a new product)

---

## 0. Document conventions

- Items marked `TODO A/B/C/D`: to be completed by A/B/C/D
- Items marked `TODO A (feedback)`: feedback-style hooks — report back if you hit issues after running on real data
- Fields marked `tentative`: may change later
- `// comments` inside JSON examples are documentation only; they don't appear in actual data

---

## 1. System skeleton

ClaimDrift consists of **1 ingestion pipeline + 5 Gemini agents**, orchestrated via Elastic Workflows, with ES|QL tools exposed to agents via the Elastic MCP server.

### 1.1 Component responsibilities (one-liner each)

| Component | Responsibility |
|------|------|
| **Ingestion Pipeline** (non-agent) | Pulls data from bioRxiv/medRxiv/Crossref/OpenAlex, writes to `preprints` index |
| **Claim Extractor** | Decomposes each preprint version into structured claims, writes to `claims` |  //gemini-2.5-flash
| **Drift Analyzer** | Diffs claim sets between v-final-preprint ↔ published, produces drift report, writes to `drift_events` |  //gemini-2.5-pro
| **Citation Finder** | Finds downstream papers citing the drifted preprint, scores severity, writes to `affected_citations` |  //gemini-2.5-flash
| **Notifier** | Drafts and sends (to test inbox) an email per affected citation, writes to `notification_log` |  //gemini-2.5-flash
| **Memory Synthesizer** | Distills drift events into reusable patterns, writes to `drift_patterns` |  //gemini-2.5-pro

### 1.2 Data flow

```
   bioRxiv/medRxiv/Crossref/OpenAlex
                  ↓ (pull)
              [Ingestion]
                  ↓
              preprints ─────┐
                  ↓          │
          [Claim Extractor]  │
                  ↓          │
                claims ──────┤
                  ↓          │
          [Drift Analyzer] ←─┤  (reads drift_patterns to condition)
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
                    [Memory Synthesizer] (async)
                             ↓
                       drift_patterns ←──┐
                                         │ (Drift Analyzer reads next time)
                                         │
```

### 1.3 Design decisions

- **Claim extraction granularity**: sentence-level
- **Drift Analyzer comparison scope**: only v-final-preprint ↔ published-journal (we don't diff v1↔v-final within the preprint itself — cleanest data)
- **Citation Finder**: uses only OpenAlex citation edges (the citation links between papers), does not fetch citing paper PDFs
- **Notifier**: one email per affected paper (no batching by author — if one author has three affected papers, they get three separate emails, no cross-paper aggregation)
- **Demo includes 2 drift events total**: the second should ideally be better than the first, showcasing the memory loop
- **Zotero / Mendeley API notifications**: via the Zotero / Mendeley APIs, tag drift-affected papers in the user's reference manager

---

## 2. Elasticsearch index list

### 2.1 Index overview

| Index | Purpose | _id | Written by | Read by |
|---------|------|-----|--------|--------|
| `preprints` | All preprint metadata + abstract | DOI (URL-decoded) | Ingestion | Claim Extractor, Drift Analyzer, frontend |
| `claims` | Claims extracted per preprint version | `{doi}::{version}::{claim_idx}` | Claim Extractor | Drift Analyzer, frontend |
| `drift_events` | One report per drift detection | UUID (auto-generated) | Drift Analyzer | Citation Finder, Memory Synthesizer, frontend |
| `affected_citations` | Downstream papers affected by a drift | `{drift_event_id}::{citing_doi}` | Citation Finder | Notifier, Memory Synthesizer, frontend |
| `drift_patterns` | Distilled, reusable drift patterns | UUID | Memory Synthesizer | **Drift Analyzer (memory loop core)**, frontend |
| `notification_log` | Email drafts + send status | `{affected_citation_id}` | Notifier | frontend |
| `dispatch_state` | Scheduled-workflow watermark (one row per flow) | `flow_name` | Elastic Scheduled Workflow | Elastic Scheduled Workflow |

**TODO D (Day 5-7)**: While building the frontend, if you find that some index isn't needed, or you need a new aggregated view (e.g., affected_citations grouped by author), or fields are insufficient — ping B and C on chat.

### 2.2 Mapping details

B fills in the mapping for each index. Below are the minimum field constraints — B adds analyzers, index options, and the ELSER semantic hookup on top.

#### 2.2.1 `preprints` index

**Minimum fields** (B must include):

- `record_source`: keyword | null (`"demo_seed"` for records written by `elastic/scripts/seed_demo_to_es.py`; real puller-ingested docs leave it unset; see §2.3)
- `doi`: keyword
- `source`: keyword (`biorxiv` | `medrxiv`)
- `version`: keyword (e.g. `v1`, `v2`)
- `is_final_preprint`: boolean
- `published_doi`: keyword | null (the final published DOI from Crossref; null means not yet published)
- `title`: text + keyword subfield
- `abstract`: semantic_text (routed through ELSER)
- `conclusion`: semantic_text | null
- `authors`: nested, containing `name` (keyword), `orcid` (keyword | null), `affiliation` (text)
- `posted_date`: date (ISO 8601)
- `ingested_at`: date

**Authoritative mapping**: [elastic/mappings/preprints.json](../elastic/mappings/preprints.json) (committed by Jeremy in the B-side port). The list above is the contract — frontend / agents should refer to it. The committed JSON adds ELSER inference endpoint, shards/replicas, refresh interval on top.

#### 2.2.2 `claims` index

**Minimum fields**:

- `record_source`: keyword | null (see §2.3)
- `claim_id`: keyword (= _id, format `{doi}::{version}::{claim_idx}`)
- `parent_doi`: keyword
- `parent_version`: keyword
- `section`: keyword (`abstract` | `conclusion`)
- `claim_idx`: integer (0-based, index within that section)
- `text`: semantic_text (routed through ELSER)
- `claim_type`: keyword (values defined in §3.1.2)
- `numerical_values`: nested | null (filled when numerical, structure in §3.1.2)
- `hedging_level`: keyword (`none` | `weak` | `strong`)  // the degree of qualifying language the author uses to express caution, uncertainty, or limited applicability of the conclusion
- `extracted_at`: date

**Authoritative mapping**: [elastic/mappings/claims.json](../elastic/mappings/claims.json).

#### 2.2.3 `drift_events` index

**Minimum fields**:

- `record_source`: keyword | null (see §2.3)
- `event_id`: keyword (UUID)
- `preprint_doi`: keyword
- `preprint_version_compared`: keyword (which version was compared against published)
- `published_doi`: keyword
- `detected_at`: date
- `drift_summary`: text (human-readable summary generated by Gemini)
- `claim_diffs`: nested, structure in §3.2.2
- `materiality_score`: float (0.0-1.0, overall "severity" of this drift)
- `retrieved_patterns_used`: keyword | null (array of `pattern_id` values the Drift Analyzer actually used in its reasoning; see §3.2.2)

**Authoritative mapping**: [elastic/mappings/drift_events.json](../elastic/mappings/drift_events.json).

#### 2.2.4 `affected_citations` index

**Minimum fields**:

- `record_source`: keyword | null (see §2.3)
- `affected_citation_id`: keyword (= _id, `{drift_event_id}::{citing_doi}`)
- `drift_event_id`: keyword
- `citing_paper_doi`: keyword
- `citing_paper_title`: text
- `citing_paper_authors`: nested (name, orcid, email | null)
- `citation_context`: text | null (if OpenAlex provides surrounding context; usually null because we don't fetch PDFs)
- `severity_tier`: keyword (`central` | `comparative` | `peripheral`)
- `severity_reasoning`: text (Gemini's justification)
- `scored_at`: date

**Authoritative mapping**: [elastic/mappings/affected_citations.json](../elastic/mappings/affected_citations.json).

#### 2.2.5 `drift_patterns` index ⭐

> This is the core index for the memory loop. Field design must let Drift Analyzer retrieve efficiently.

**Minimum fields**:

- `record_source`: keyword | null (see §2.3)
- `pattern_id`: keyword (UUID)
- `pattern_description`: semantic_text (human-readable + ELSER searchable, **this is the memory loop's retrieval field**)
- `pattern_type`: keyword (`numerical_softening` | `hedging_addition` | `claim_disappearance` | `effect_size_reduction` | `other`) // TODO A: may expand after Memory Synthesizer prompt iteration
- `domain_tags`: keyword (array, e.g. `["covid-19", "clinical-trial", "rct"]`)
- `source_event_ids`: keyword (array, list of drift_events that produced this pattern)
- `support_count`: integer (number of drift_events supporting this pattern — higher = more reliable)
- `created_at`: date
- `last_updated_at`: date

**Authoritative mapping**: [elastic/mappings/drift_patterns.json](../elastic/mappings/drift_patterns.json). `pattern_description` is wired through ELSER (`semantic_text` with `.elser-2-elastic` inference id) — this is verified end-to-end by the Phase 4 `search_drift_patterns` ES|QL tool (changelog 2026-05-23).

#### 2.2.6 `notification_log` index

**Minimum fields**:

- `record_source`: keyword | null (see §2.3)
- `affected_citation_id`: keyword (= _id)
- `drift_event_id`: keyword
- `recipient_email`: keyword
- `subject`: text
- `body`: text
- `status`: keyword (`drafted` | `sent` | `bounced` | `failed`)
- `sent_at`: date | null
- `error_message`: text | null

**Authoritative mapping**: [elastic/mappings/notification_log.json](../elastic/mappings/notification_log.json).

#### 2.2.7 `dispatch_state` index

Watermark for the §9.6.1 scheduled trigger workflow. One document per logical
flow; the flow uses `last_seen_ingested_at` as the cursor for "what's new
since last poll" so it never re-dispatches a pair it already saw.

Today there is exactly one row: `flow_name="main_flow"`, tracking new
`(preprint, published)` pairs landing in `preprints`. A future second flow
(e.g. a re-process backfill, a re-evaluation of stale drift_events) would add
its own row with its own `flow_name`.

**Minimum fields**:

- `record_source`: keyword | null (see §2.3 — left unset for production rows)
- `flow_name`: keyword (= _id, e.g. `main_flow`)
- `last_seen_ingested_at`: date (the cursor)
- `last_updated_at`: date (when the workflow last touched this row)

**Authoritative mapping**: [elastic/mappings/dispatch_state.json](../elastic/mappings/dispatch_state.json).

### 2.3 Demo seed vs real-data tagging (`record_source`)

To keep demo records visually inspectable in ES while still letting real-data views exclude them, every index carries an optional `record_source` keyword field.

- **Real puller-ingested docs**: field left **unset**. No code path should ever write `record_source` for real data.
- **Demo records**: tagged with `record_source="demo_seed"`. The tag is **not** written into the seed JSON files under `elastic/demo_seed/*.json` — those files contain only business fields. Instead, [elastic/scripts/seed_demo_to_es.py](../elastic/scripts/seed_demo_to_es.py) injects the constant at bulk-index time (see `DEMO_RECORD_SOURCE` and the `tagged_row = {"record_source": DEMO_RECORD_SOURCE, **row}` line). This keeps the seed files clean and guarantees no demo record can be written without the tag.
- **Real-data views** (BFF in Elasticsearch mode, agent retrieval against real data) exclude demo records with `must_not: [{"term": {"record_source": "demo_seed"}}]`. See `apps/bff/mock_server.py` for the canonical filter.
- **Values seen so far**: `"demo_seed"`. New values (e.g. a future `"backfill_2026q3"`) require a §8.1 add-field notice.

---

## 3. Agent input/output JSON skeletons

All agent input/output is JSON. Naming conventions in §7. Agents are chained via Elastic Workflows, but each agent has its own input/output schema in Agent Builder so it can be tested in isolation.

### 3.1 Claim Extractor (Agent 1)

#### 3.1.1 Input

```json
{
  "preprint_doi": "10.1101/2024.01.15.123456",        // required
  "version": "v3",                                     // required
  "title": "...",                                      // required, read from preprints index
  "abstract": "...",                                   // required
  "conclusion": "..."                                  // optional, null means no conclusion section
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
      "claim_idx": 0,                                  // 0-based within this section
      "text": "Hydroxychloroquine reduced viral load by 45% in COVID-19 patients.",
      "claim_type": "quantitative",                    // see enum below
      "numerical_values": [                            // optional, only when claim contains numbers
        {
          "metric": "viral_load_reduction",            // tentative: A defines this field in the prompt
          "value": 45.0,
          "unit": "percent",
          "comparison": "reduction"                    // "reduction" | "increase" | "ratio" | "absolute"
        }
      ],
      "hedging_level": "none"                          // "none" | "weak" | "strong"
    }
  ],
  "extraction_metadata": {
    "model": "gemini-2.5-flash",                       // current v0; revisit gemini-3.5-flash before submission
    "extracted_at": null                               // orchestrator fills on receive (Cloud Run dispatcher per §9.6.1); same rationale as §3.2.2 analyzed_at, §3.4.2 drafted_at, §3.5.2 synthesized_at — LLMs cannot read a real clock
  }
}
```

**NOTE (v0 finding, 2026-05-21)**: In v0 smoke tests the `comparison` subfield occasionally returned `null` when the sentence explicitly used reduction/increase verbs (e.g. "reduced X by 45%"). Prompt should enforce non-null when such verbs are present. Tracked as A-issue in `agents/README.md` v0 status section.

**`claim_type` enum** (tentative, A may adjust in the prompt):

| Value | Meaning |
|----|------|
| `qualitative` | Non-numerical conclusion. "X improves Y" |
| `quantitative` | Conclusion with numbers. "X reduced Y by 45%" |
| `causal` | Causal claim. "X causes Y" |
| `correlational` | Correlation. "X is associated with Y" |
| `hedged` | Explicitly hedged. "X may improve Y" |

Note: a claim can be both `quantitative` + `causal`, but in v0 we take only the primary tag. If A finds during prompt iteration that multiple tags are needed, change to an array (field type change → see §8 evolution rules).

**TODO A**: If during prompt iteration you find `numerical_values` subfields are insufficient (e.g., need `confidence_interval`), report on chat.

### 3.2 Drift Analyzer (Agent 2)

#### 3.2.1 Input

```json
{
  "preprint_doi": "10.1101/2024.01.15.123456",
  "preprint_version_compared": "v3",                   // typically the final preprint version
  "published_doi": "10.1016/j.cell.2024.05.001",
  "preprint_claims": [ /* claim objects from claims index */ ],
  "published_claims": [ /* claim objects from claims index */ ]
}
```

**Retrieval rules** (critical for the memory loop):

- Drift Analyzer calls the `search_drift_patterns` MCP tool itself (no longer pre-injected by the orchestrator) using the joined preprint claim texts as the query, `top_k=3`.
- **No score threshold.** The tool's `_score` is ELSER similarity over a `semantic_text` field and is not usable as a fixed cutoff in our index size — same finding that retired the threshold for Memory Synthesizer (see §3.5.1 and changelog 2026-05-22 / 2026-05-23). The agent reads each retrieved pattern's `pattern_description` and judges relevance per-candidate.
- Patterns the agent actually used in its reasoning are echoed back as `retrieved_patterns_used` in the §3.2.2 output (an array of pattern ids).

#### 3.2.2 Output

```json
{
  "event_id": null,                                    // orchestrator fills on receive (supervisor §9.6.1 mints uuid4); same rationale as analyzed_at — LLM cannot reliably mint a v4 UUID. Dispatcher has a secondary `event_id or uuid()` fallback at main.py:151 in case supervisor is bypassed.
  "preprint_doi": "...",
  "preprint_version_compared": "v3",
  "published_doi": "...",
  "drift_summary": "Effect size for HCQ on viral load was reduced from 45% to 12%, and a hedging qualifier was added in the published version.",
  "claim_diffs": [
    {
      "diff_type": "numerical_shift",                  // see enum below
      "preprint_claim_id": "10.1101/...::v3::abstract::0",
      "published_claim_id": "10.1016/...::v1::abstract::0",
      "preprint_text": "...",
      "published_text": "...",
      "change_description": "Effect size reduced from 45% to 12%",
      "numerical_delta": {                             // optional, only when diff_type is numerical_shift
        "metric": "viral_load_reduction",
        "preprint_value": 45.0,
        "published_value": 12.0,
        "absolute_delta": -33.0,
        "relative_delta": -0.733                       // -73.3%
      }
    }
  ],
  "materiality_score": 0.82,                           // 0.0-1.0, overall severity
  "retrieved_patterns_used": [                         // which retrieved patterns actually entered the reasoning
    "pattern-uuid-1",
    "pattern-uuid-2"
  ],
  "analyzed_at": "2026-05-20T..."
}
```

**`diff_type` enum** (tentative):

| Value | Meaning |
|----|------|
| `claim_disappeared` | A claim present in the preprint is entirely gone in the published version |
| `claim_added` | A claim newly present in the published version |
| `numerical_shift` | Same claim, different numerical value |
| `hedging_added` | The published version added hedging language |
| `hedging_removed` | The published version removed hedging (rare) |
| `claim_reversed` | The conclusion direction is reversed (most severe) |

**NOTE (v0 finding, 2026-05-21)**: In v0 testing, Drift Analyzer (Pro) spontaneously detected scope-narrowing drift (e.g. "in COVID-19 patients" → "in early-stage COVID-19 patients") but had to shoehorn it into `hedging_added`. Consider adding `scope_restricted` to the enum. Pending team discussion per §8.1 (rename/type change rules).

**materiality_score scoring guide** (tentative, reference for A when writing the prompt):

- `0.0-0.3`: minor adjustment (wording change, decimal-level correction)
- `0.3-0.6`: medium (numerical change < 50%, added hedging)
- `0.6-0.9`: significant (numerical change > 50%, claim disappeared)
- `0.9-1.0`: major (conclusion reversed, significance lost)

**TODO A (feedback)**: After running a few real cases, if these thresholds seem off (e.g., most real cases cluster in 0.3-0.6 and the buckets don't discriminate), report on chat — C will adjust.

### 3.3 Citation Finder (Agent 3)

#### 3.3.1 Input

```json
{
  "drift_event_id": "uuid-...",
  "preprint_doi": "10.1101/...",                       // find who cited it
  "drift_summary": "...",                              // copied from drift_event, for Gemini to judge severity
  "claim_diffs": [ /* copied from drift_event */ ]
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
          "email": "jane@example.edu"                   // null is fine; demo uses team test inboxes
        }
      ],
      "citation_context": null,                         // almost always null, OpenAlex usually doesn't provide
      "severity_tier": "central",                       // "central" | "comparative" | "peripheral"
      "severity_reasoning": "The citing paper builds its main conclusion on the 45% effect size, which has now been revised to 12%. This invalidates their primary argument."
    }
  ],
  "total_found": 47,                                    // how many citing papers found in total
  "processed": 47,                                      // how many were actually processed (rate limits may skip some)
  "found_at": "2026-05-20T..."
}
```

**`severity_tier` judgment guide** (reference for A when writing the prompt):

- `central`: the drifted claim is the citing paper's main argument
- `comparative`: the drifted claim is used for comparison/reference, not core
- `peripheral`: the drifted claim is only mentioned once in related work

**NOTE (v0 finding, 2026-05-21; resolved 2026-05-24)**: Citation Finder v0 fabricated plausible-looking DOIs because no `openalex_puller` tool was wired. **Phase 5d** wired `openalex_citing_works` (Elastic Workflow YAML tool calling OpenAlex REST). **Phase 5e** replaced the v0 fabrication INSTRUCTION with real-DOI lookup via that MCP tool — see the 2026-05-24 5e changelog entry. The fabrication scaffold (sentinel DOIs, `SYNTHETIC_V0_PLACEHOLDER` markers) is retired; the agent's output now contains only real OpenAlex DOIs and can be written to the `affected_citations` index. **Important caveat (still applies)**: smoke-test envelopes for `citation_finder` must use REAL preprint DOIs (from `preprints` index where `record_source != "demo_seed"`); OpenAlex returns `meta.count: 0` for synthetic demo DOIs so demo seed data cannot validate the agent's happy path — see 5e changelog for the `10.1101/2023.07.26.23293038` NAFLD-biomarkers test case (4 real citing works retrieved).

**TODO A (feedback)**: After running demo cases, if the three tiers feel insufficient (e.g., you want to add a "background" tier), or if the guide makes Gemini confused, report on chat.

### 3.4 Notifier (Agent 4)

#### 3.4.1 Input

```json
{
  "affected_citation_id": "...",
  "drift_event_summary": "...",                         // taken from drift_event
  "claim_diffs": [ /* drifted claim list */ ],
  "citing_paper_doi": "...",
  "citing_paper_title": "...",
  "recipient": {
    "name": "Jane Doe",
    "email": "test+jane@team-mailbox.com",              // demo phase: all sent to team test inboxes
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
  "drafted_at": null,                                   // orchestrator fills on receive (Cloud Run dispatcher per §9.6.1). LLMs cannot read a real clock; left null in agent output, mirroring §3.2.2 analyzed_at and §3.5.2 synthesized_at.
  "dispatch": {                                         // in v0 the agent ONLY drafts; actual send is a separate orchestrator step (B's SMTP/Gmail code)
    "status": "drafted",                                // v0 always "drafted". Post-v0 enum after dispatch step runs: "sent" | "bounced" | "failed" | "skipped"
    "sent_at": null,                                    // null until the orchestrator's dispatch step writes notification_log
    "error_message": null
  }
}
```

**Email tone guide** (reference for A when writing the prompt):

- Neutral, informational, **no lecturing or blame**
- Quote the drifted claim verbatim + the published version verbatim + links
- Explain the severity and the reason
- Explicit disclaimer: this is an automated detection notice; the author judges whether updating is needed

**NOTE (v0 finding, 2026-05-21)**: In v0, the Notifier quoted phrase fragments (e.g. "reduced viral load by 45%") instead of full sentences. Prompt must enforce quoting the complete sentence (subject + verb + object + modifiers) so authors can locate the source unambiguously.

### 3.5 Memory Synthesizer (Agent 5) ⭐

> The write side of the memory loop. This agent fires asynchronously (after the drift_event is written), without blocking the main flow.

#### 3.5.1 Input

```json
{
  "trigger": "new_drift_event",                          // what caused this
  "drift_event_id": "uuid-...",
  "drift_event": { /* full drift_event */ },
  "affected_citations_summary": {                        // aggregated info, not the full list
    "total_affected": 47,
    "central_count": 8,
    "comparative_count": 21,
    "peripheral_count": 18
  }
}
```

**Retrieval rules** (Memory Synthesizer calls `search_drift_patterns` itself; no patterns are pre-injected):

- Query with `drift_event.drift_summary` as `query_text`, `top_k=5`.
- **No score threshold.** The tool's `_score` is ELSER similarity over a `semantic_text` field; see changelog 2026-05-23 "RRF over pattern_description was fusing the same source twice" — `_score` spreads but still misranks, so it is not usable as a fixed cutoff. The original §3.5.1 v0 design used `similarity_score >= 0.75` as a hard rule; that rule has been retired.
- The agent is instead instructed to **read** each retrieved pattern's `pattern_description` and judge "is this the same underlying phenomenon as the new drift_event?" per-candidate. Decision is made by the LLM, not by the score.
- If yes → call `update_drift_pattern(pattern_id, source_event_id, now_iso)`. If no candidate qualifies → call `create_drift_pattern(...)`. The two-tool shape (rather than a single upsert) keeps the create-vs-update decision visible in the tool the LLM chooses.

**v0 update scope** (intentional minimum):

- `update_drift_pattern` only appends `source_event_id` to `source_event_ids` and refreshes `last_updated_at`. `pattern_description` / `domain_tags` are not refined.
- **TODO (post-v0)**: extend `update_drift_pattern` with optional `pattern_description_refinement` and `domain_tags_to_add` parameters, so a long-lived pattern can broaden its description as new domains accumulate (e.g. "COVID-related" → "COVID-related and other respiratory virus" once cross-domain events show up). Defer until prompt iteration shows v0's narrow-description failure mode is hurting retrieval quality.

**drift_event ← pattern back-link** (out of scope for this agent):

- Memory Synthesizer does NOT write `pattern_id` back into `drift_event.retrieved_patterns_used`. That double-link is the orchestrator's job (an Elastic Workflows step running after the agent), so the agent stays single-purpose.

#### 3.5.2 Output

```json
{
  "action": "create_new",                                // "create_new" | "update_existing"
  "pattern": {
    "pattern_id": "uuid-...",                            // newly generated on create; reuse old id on update
    "pattern_description": "COVID-related clinical preprints frequently show 50%+ reduction in reported effect size between final preprint and published version, often with added hedging language.",
    "pattern_type": "effect_size_reduction",
    "domain_tags": ["covid-19", "clinical-trial"],
    "source_event_ids": ["uuid-..."],                    // only this one on create; append on update
    "support_count": 1,                                  // 1 on create; +1 on update
    "created_at": "2026-05-20T...",
    "last_updated_at": "2026-05-20T..."
  },
  "synthesized_at": null                                 // agent leaves null; orchestrator fills it post-call (mirrors drift_event.analyzed_at)
}
```

**pattern_description writing guide** (reference for A when writing the prompt):

- Must be a **reusable** summary, not just a description of this one event
- Must include: which domain + what type of drift + rough magnitude
- Must not include specific DOIs or author names (that's what source_event_ids is for)
- 30-80 words in English; later injected into Drift Analyzer's prompt context

**NOTE (v0 finding, 2026-05-21)**: v0 produces high-quality `pattern_description` (correctly avoids leaking specific DOIs / drug names). One observation: v0 returned only 2 `domain_tags` (e.g. `["medicine", "virology"]`) while §3.5.2 example shows 3-5. Prompt should encourage 3-6 tags mixing general + specific.

---

## 4. Agent invocation order and orchestration

### 4.1 Main flow (synchronous)

```
[Ingestion completes, preprints index gets a new/updated record]
    ↓
trigger: detected that a preprint now has a published_doi
    ↓
[Claim Extractor]  runs twice: once for preprint final version, once for published version
    ↓
[Drift Analyzer]  retrieval-then-reason
    ↓
[Citation Finder]
    ↓
[Notifier]  fires once per affected citation
    ↓
done
```

### 4.2 Side flow (asynchronous)

```
[Drift Analyzer completes, drift_events gets a new record]
    ↓ (async trigger, non-blocking)
[Memory Synthesizer]
    ↓
drift_patterns created/updated
```

### 4.3 Workflow implementation

**Status (2026-05-23)**: partial.

- ✅ **Memory loop sub-flow (§4.2)** is end-to-end live. `memory_synthesizer` runs on Vertex AI Agent Engine and calls three Elastic Agent Builder tools via the Elastic MCP server: `search_drift_patterns` (ES|QL tool) + `create_drift_pattern` / `update_drift_pattern` (Workflow YAML tools). See `elastic/agent_builder/workflows/` and §9.3.
- ❌ **Main synchronous flow (§4.1)** — Claim Extractor → Drift Analyzer → Citation Finder → Notifier chaining — is NOT yet wired as an Elastic Workflow. Each of the four agents works standalone (verified via `adk web`), but no orchestrator hands the output of one to the next automatically. **Open question for the team**: do we need this for the hackathon demo, or can the demo narrate the flow by manually feeding each agent in turn? Decision blocks `4c` (demo video). The top-level `workflows/` directory (distinct from `elastic/agent_builder/workflows/`) is intentionally empty pending this decision.

---

## 5. Ingestion Pipeline interface

Each puller's job: pull data → normalize → bulk write to `preprints` index (conforming to §2.2.1).

### 5.1 Puller list

| Puller | Source | Trigger | Target index |
|--------|--------|---------|-----------|
| `biorxiv_puller` | bioRxiv REST | Cloud Scheduler, hourly | `preprints` |
| `medrxiv_puller` | medRxiv REST | Cloud Scheduler, hourly | `preprints` |
| `crossref_puller` | Crossref Event Data webhook + REST fallback | Webhook + Cloud Scheduler as backup | updates `published_doi` field of `preprints` |
| `openalex_puller` | OpenAlex REST | on demand (triggered by Citation Finder) | (does not write ES directly; returns to agent) |

### 5.2 General rules (B must follow)

- All outbound requests carry polite-pool headers (`User-Agent` includes contact email)
- Use bulk API for ES writes
- DOI normalization: lowercase, no `https://doi.org/` prefix
- Writes are upsert (existing DOI is updated)

**Status (2026-05-26)**: Live on Cloud Run + Cloud Scheduler. 3 Cloud Run Jobs deployed (`claimdrift-biorxiv-puller`, `claimdrift-medrxiv-puller`, `claimdrift-crossref-puller`) with `us-central1` Schedulers at `0/10/30 * * * *` (staggered hourly). `preprints` index has ~10k real records + ~2.2k real `(preprint, published)` pairs (`record_source != "demo_seed"`). See [docs/ingestion_cloud_run_ops.md](ingestion_cloud_run_ops.md) for the operational runbook (image, args, validation queries). arxiv source removed from scope (2026-05-26) — single-vertical bioRxiv+medRxiv coverage is sufficient for the §3.5 memory-loop demonstration and the §4.1 end-to-end flow, and dropping it keeps the OAI-PMH XML parsing complexity out of the ingest path.

### 5.3 ELSER semantic hookup

The `semantic_text` fields in `preprints`, `claims`, and `drift_patterns` must be wired through ELSER semantic retrieval.

**Serverless implementation note (B)**: Use `semantic_text` with an explicit `.elser-2-elastic` `inference_id`. Do not attach an ingest inference pipeline that writes ELSER output back into the same `semantic_text` field, because `semantic_text` expects the indexed document field to remain a scalar text value.

---

## 6. BFF / Server-Sent Events event format

The frontend receives real-time agent state from the BFF via SSE. The event envelope below is the **shape the frontend consumes** — it is NOT what the underlying agent runtimes natively emit (see §6.2 transport status), so a BFF-side translation layer is required.

### 6.1 Event type list (C drafts, D gives feedback)

> **Status (Phase 4b complete, 2026-05-23)**: this section defines the **frontend-facing contract**. Vertex AI Agent Engine (Phase 4b runtime for `memory_synthesizer`) does NOT natively emit events in this shape — its `streamQuery` endpoint streams ADK's own event format (function-call / function-response / text events). A BFF-side adapter translating Agent Engine's stream to the `{event_type, agent_id, drift_event_id, timestamp, payload}` envelope below is **not yet implemented** (TODO B + C; see §6.2).
>
> Until the adapter exists, the frontend can either (a) call the Agent Engine `run` endpoint and parse ADK's native event format directly (faster, looser typing), or (b) wait for the adapter. Pick on chat.

C decides which logical events to surface when wiring the adapter; D is the frontend consumer and has the final say on event granularity and payload content.

All events include common fields:

```json
{
  "event_type": "...",
  "agent_id": "claim_extractor" | "drift_analyzer" | "citation_finder" | "notifier" | "memory_synthesizer",
  "drift_event_id": "uuid-..." | null,                   // ties together all events for the same drift event
  "timestamp": "2026-05-20T...",
  "payload": { /* see event types */ }
}
```

Event types:

| event_type | When emitted | payload |
|-----------|----------|---------|
| `agent.started` | agent begins execution | `{ input_summary }` |
| `agent.tool_call` | agent invokes any MCP tool (ES\|QL or workflow) | `{ tool_name, args }` |
| `agent.pattern_retrieved` | **Drift Analyzer / Memory Synthesizer retrieved patterns (memory loop key event, frontend should highlight)** | `{ pattern_ids, scores }` — `scores` is informational/diagnostic ONLY; do NOT use it to rank or threshold in the UI (see changelog 2026-05-23 RRF finding). The agent's choice of which pattern to act on is the authoritative signal — frontend should highlight `pattern_ids` echoed by the subsequent `agent.tool_call` to `update_drift_pattern`. |
| `agent.step` | agent completes a reasoning step | `{ step_name, summary }` |
| `agent.completed` | agent finishes | `{ output_summary, output_id }` |
| `agent.failed` | agent errors | `{ error_message, retry_count }` |

**TODO D (Day 8-10)**: While building the agent activity timeline, if you find:

- Missing event types (e.g., an "agent paused waiting for retrieval results" intermediate state)
- Insufficient payload info (e.g., `agent.completed` should include actual output snippets, not just a summary)
- Need extra fields on some events (e.g., `agent.pattern_retrieved` should include the full pattern_description, not just the id)

Ping C on chat; C adjusts the emit config in Agent Builder.

### 6.2 SSE transport details

**TODO B (Day 5-6)**: SSE channel design, heartbeat interval, reconnection protocol, frontend subscription approach.

---

## 7. Global naming + encoding conventions

### 7.1 Naming

- **Field names**: snake_case (e.g. `preprint_doi`, not `preprintDoi`)
- **enum values**: lowercase with underscores (e.g. `numerical_shift`, not `NUMERICAL_SHIFT` or `numericalShift`)
- **Index names**: plural, lowercase, underscores (e.g. `drift_events`, not `DriftEvent`)
- **Agent IDs**: lowercase underscores (e.g. `drift_analyzer`)

### 7.2 Encoding

- **Time**: ISO 8601 UTC with Z suffix (`2026-05-20T12:34:56Z`), not local timezone
- **DOI**: normalized to the bare path, no `https://doi.org/` prefix, lowercase. Example: `10.1101/2024.01.15.123456`
- **Nullable fields**: explicit `null`, not empty strings
- **IDs**: UUID v4 (unless a natural key exists)
- **JSON**: no trailing commas, strings in double quotes

### 7.3 Field type conventions

- Integer counts use `integer`, not `long` (our scale doesn't need it)
- Scores and probabilities use `float`, range 0.0-1.0
- Free text uses `text`; short strings needing exact match use `keyword`; semantic-searchable text uses `semantic_text`

---

## 8. Interface evolution rules

After the skeleton is locked, field details will keep evolving. The rules below keep the evolution from getting chaotic.

### 8.1 Change classification

| Change type | Expected frequency | Process |
|---------|----------|------|
| **Add new field** | High (~70%) | Whoever needs it adds it; commit + notify on chat; add a line to the changelog under the relevant section of this doc |
| **Rename field or change type** | Medium (~20%) | Proposer opens a GitHub issue or pings the relevant parties on chat; C decides within 24h; everyone changes at once and tests together |
| **Change ES mapping** | Low (~5%) | B pings C before changing; adding fields is fine; type changes require index rebuild (demo data is small, can rerun in hours) |
| **Change agent invocation order or responsibility** | Very low (~5%) | Architecture-level change, hold a 30-minute meeting, C changes the workflow personally |

## 9. Production deployment architecture (Phase 4 + Phase 5)

§3–§8 describe the **business semantics** of the system. §9 describes the **production deployment shape** required by the Google Cloud Rapid Agent Hackathon — Elastic Track. v0 (Phases 1-3) ran locally via `adk web` against a serverless Elasticsearch project; Phase 4 moved the memory-loop slice (`memory_synthesizer` + 3 drift tools) onto Elastic MCP + Vertex AI Agent Engine; Phase 5 (§9.6) extends the same pattern to the remaining 4 agents and adds the §4.1 main-flow orchestration.

### 9.1 Rules-driven constraints

The hackathon rules ([rapid-agent.devpost.com/rules](https://rapid-agent.devpost.com/rules)) impose three hard constraints that determine the deployment shape:

1. **Gemini-only for project-runtime AI.** All other AI tools are not permitted in the running project. (Auxiliary dev tooling like AI-assisted code editors is not in scope.)
2. **Project must use Google Cloud Agent Builder.** The Agent Builder platform now bundles a code-first SDK (ADK), a managed runtime (Vertex AI Agent Engine), and Agent Studio. **ADK code deployed to Agent Engine satisfies this requirement** — confirmed via the official "Agent Builder → ADK overview" docs ([cloud.google.com/products/agent-builder](https://cloud.google.com/products/agent-builder)). We do NOT have to rewrite the Phases 1-3 ADK agents.
3. **Elastic track requires integrating the Partner Entity's MCP server.** Elastic Agent Builder ships a built-in MCP server that natively exposes whatever custom tools we define inside it. We MUST route tool calls through this server (not a self-written MCP shim) to satisfy the rule literally.

### 9.2 Target architecture

```
Vertex AI Agent Engine (managed)
   - claim_extractor       (ADK LlmAgent, gemini-2.5-flash) ✓ deployed (Phase 5c)
   - drift_analyzer        (ADK LlmAgent, gemini-2.5-pro)   ✓ deployed (Phase 5a)
   - citation_finder       (ADK LlmAgent, gemini-2.5-flash) ✓ deployed (Phase 5e)
   - notifier              (ADK LlmAgent, gemini-2.5-flash) ✓ deployed (Phase 5b)
   - memory_synthesizer    (ADK LlmAgent, gemini-2.5-pro)   ✓ deployed (Phase 4b)
        │
        │ MCP protocol (tool calls)
        ▼
Elastic Agent Builder built-in MCP server
   - search_drift_patterns        (ES|QL tool)              ✓ live (Phase 4a-2)
   - create_drift_pattern         (Elastic Workflow YAML)   ✓ live (Phase 4a-3)
   - update_drift_pattern         (Elastic Workflow YAML)   ✓ live (Phase 4a-4)
   - openalex_citing_works        (Elastic Workflow YAML)   ✓ live (Phase 5d)
        │
        │ Elasticsearch APIs / OpenAlex API
        ▼
Elasticsearch Serverless (drift_patterns / drift_events / claims / ...)
```

**Deployment status (2026-05-24)**: 5 of 5 agents on Agent Engine (`memory_synthesizer` / `notifier` / `claim_extractor` / `drift_analyzer` / `citation_finder`); 4 of 4 MCP tools live. Remaining: 5f/5g main-flow orchestration — see §9.6.

### 9.3 Tool migration plan (function tool → Elastic Agent Builder tool)

Phases 1-3 implemented the three drift tools as Python functions in `agents/_shared/elastic_retrieval.py` and `agents/_shared/elastic_write.py`. The behavioral contract (top-k retrieval surfaces candidates; LLM does its own rerank by reading `pattern_description`; UUID v4 mint on create; GET-then-merge-dedup-then-PUT on update; 404-raises-loudly) is spec-frozen — Phase 4a re-expresses them inside Elastic Agent Builder while preserving every observable behavior. Phase 5d adds a fourth tool (`openalex_citing_works`) that has no Phase 1-3 Python predecessor — it's a Workflow YAML wrapper over the OpenAlex REST API, replacing `citation_finder`'s v0 fabrication scaffold (§3.3 NOTE).

| Python tool | Target shape in Elastic Agent Builder | Notes |
|---|---|---|
| `search_drift_patterns` | **ES\|QL tool** — single-`MATCH` query against `pattern_description` (semantic_text, ELSER-routed). Parameters: `query_text`, `top_k`, `exclude_demo_seed`. | No `retriever.rrf` wrapper — see 2026-05-23 changelog finding. The Phase 1 RRF design was the same source fused with itself on this index, so single-`MATCH` is strictly simpler and not less expressive. The Python implementation in `agents/_shared/elastic_retrieval.py` stays in the repo as reference spec (not runtime). |
| `create_drift_pattern` | **Elastic Workflow** (YAML) — single `elasticsearch.request` step: `PUT _doc/{id}?op_type=create&refresh=wait_for`. UUID v4 and ISO 8601 `now_iso` are pushed up to the caller (the LLM) because Workflows YAML has no native `uuid()` / `now()` step in the current preview. `record_source` left unset per §2.3. | `op_type=create` makes (vanishingly rare) UUID collisions surface as an ES error instead of silent overwrite. Array inputs (`domain_tags`) MUST use Liquid type-preserving syntax `"${{ inputs.domain_tags }}"` — bare `{{ }}` stringifies arrays/objects/numbers/booleans (caught and fixed during Phase 4a-5; see 2026-05-23 changelog). |
| `update_drift_pattern` | **Elastic Workflow** (YAML) — single `elasticsearch.request` step: `POST _update/{id}?refresh=wait_for` with a painless script that performs the read-modify-write atomically on the ES side: set-union dedup of `source_event_ids`, recompute `support_count = source_event_ids.size()`, refresh `last_updated_at`, preserve `created_at` / `pattern_description` / `pattern_type` / `domain_tags`. 404 on the underlying `_update` surfaces as a workflow step failure (`document_missing_exception`), not a silent fallback to create. | Painless was chosen over a two-step GET→PUT to (a) keep atomicity / version-token protection, and (b) avoid relying on Workflows YAML expression language to do array set-union between steps (Liquid doesn't expose set ops). v0 append-evidence-only scope held: `pattern_description` / `domain_tags` are intentionally NOT refined here — see §3.5.1 post-v0 TODO. |
| `openalex_citing_works` (Phase 5d) | **Elastic Workflow** (YAML) — two chained `http` steps: step 1 GETs `api.openalex.org/works/https://doi.org/{doi}` to resolve the source paper's OpenAlex short id; step 2 GETs `api.openalex.org/works?filter=cites:{short_id}&per-page={n}` to list citing works. Step 2 references step 1 via `steps.fetch_source.output.data.id` (the `http` step output envelope shape; see Phase 5d Spike findings in changelog). Returns OpenAlex's native `{meta, results[]}` shape; `citation_finder` (5e) is responsible for mapping `results[].{doi,title,authorships,publication_year,cited_by_count}` to §3.3.2 `affected_citations[]` fields and assigning per-citation `severity_tier`. | No Phase 1-3 Python predecessor; the closest analog is `ingestion/pullers/openalex_client.py` which Jeremy wrote as an on-demand HTTP client (NOT a puller that pre-indexes into ES — the 200M-work OpenAlex corpus is too large to preload). The Workflow YAML is a thin wrapper around the same OpenAlex REST calls. URL-encoding of the DOI path segment relies on OpenAlex's server-side path normalization rather than Liquid's `cgi_escape` (which is non-functional in this Workflows build per Spike 1). |

### 9.4 Agent migration plan (ADK local → Agent Engine)

| Phase 3 (local) | Phase 4 (managed) |
|---|---|
| `adk web` running `agents/memory_synthesizer/agent.py` | `adk deploy agent_engine agents/memory_synthesizer` |
| `LlmAgent(tools=[FunctionTool(search_drift_patterns), ...])` — direct Python imports | `LlmAgent(tools=[<MCP tool reference to Elastic Agent Builder server>, ...])` — same INSTRUCTIONs, same model, same business semantics |
| Sessions live in-process | Sessions persisted by `VertexAiSessionService` (auto-wired by `reasoning_engines.AdkApp`) |
| Auth: `.env` API key on the developer machine | Auth: Workload Identity for Agent Engine → Vertex; Elasticsearch API key passed through MCP client config |

INSTRUCTIONs do not change. Tool *names and schemas* do not change. The decision-quality experiments validated in Phase 3 (LLM-judged create-vs-update, no score threshold) carry over unmodified.

### 9.5 What stays Python forever

These do NOT migrate — they remain Python utility code in the repo:

- `agents/scripts/probe_rrf_scores.py` — diagnostic, not a runtime path
- `agents/_shared/elastic_retrieval.py` / `elastic_write.py` — kept as **reference spec + smoke-test harness**. If a future change to the ES\|QL tool or Workflow YAML causes a behavior regression, we run these Python implementations against the same payload to bisect whether the regression is in Elastic-side translation or in the LLM prompt.

### 9.6 Scope re-evaluation & Phase 5 plan

**Trigger (2026-05-23)**: after Phase 4b shipped, the question "is the project ready for the demo video?" surfaced an unresolved assumption: Phase 4 deployed only `memory_synthesizer`, while §1.1 / §4 promise a 5-agent system. C's first instinct was that the hackathon rules allow narrow-deep ("a functional agent" — singular) and that a video focused on the memory loop would be enough. That instinct was wrong.

**Re-read of Devpost judging evidence**:

1. **Rules require runnability, not just demo-watchability.** Quote: the repository "must contain all necessary source code, assets, and instructions required for the project to be functional," with the URL provided "for judging and testing." Judges have stated intent to install and run, not only watch the video.
2. **Stage One screen requires "reasonably apply both ... Partner and Google Cloud products."** With only 1 of 5 agents on Agent Engine / MCP, the other 4 agents apply neither — they are local-only Python that doesn't touch the partner stack at all. That is hard to defend as "reasonably applied" if a judge clones and inspects.
3. **Stage Two is four equal-weighted criteria** (Technological Implementation / Design / Potential Impact / Quality of the Idea, 25% each). A claimed-but-undeployed 4/5 of the agents materially hurts the Technological Implementation score, and indirectly the Design / Impact scores (because the demo can't show the full pipeline working).
4. **Base rate among recent agent-themed Google Cloud hackathon winners is multi-agent breadth.** ADK Hackathon 2025 Grand Prize was a 4-capability multi-agent SDR system; GKE EMEA Winner was 6 specialized agents; narrow-deep single-capability wins exist (GKE Turns 10 Grand Prize) but are the exception. The recurring failure mode in judge interviews is "broad scope claimed in the description, none of it actually runs in the demo."

**Conclusion**: Phase 4 is necessary but not sufficient. The "demo video unblocked" call in the 2026-05-23 Phase-4-complete changelog entry was premature — it correctly captures that the memory loop (the project's hardest engineering bet, and the only piece with non-trivial Elastic Workflow YAML + Painless write-side complexity) is done, but it skipped the requirement that the surrounding 4 agents also need to be on Agent Engine + reachable via MCP for the full §4.1 main flow to run as advertised. **Phase 5 closes that gap before the demo.**

**Phase 5 scope (in order of dependency)**:

| Step | What | Reuses from Phase 4 | New work |
|---|---|---|---|
| **5a. `drift_analyzer` MCP-ify + deploy** | Replace direct Python import of `search_drift_patterns` with the same `McpToolset(tool_filter=["search_drift_patterns"])` pattern proven in `memory_synthesizer`. Inline any `_shared` constants. Deploy via `adk deploy agent_engine`. | All of 4b-2's wiring; same Kibana MCP endpoint; same API key | Verify `search_drift_patterns` returns the shape the agent's INSTRUCTION expects (same call site as `memory_synthesizer`, so high confidence; quick E2E smoke test) |
| **5b. `notifier` deploy (no MCP tools)** | `notifier` has no tools — it just drafts JSON. Inline `_shared.config.MODEL_FLASH` and `adk deploy agent_engine`. | All of 4b-5's deploy machinery (env stripping, requirements pruning) | None |
| **5c. `claim_extractor` deploy (no MCP tools)** | Same shape as `notifier` — pure LLM extraction, no tools. Inline + deploy. | All of 4b-5 | None |
| **5d. `openalex_citing_works` MCP tool** ✓ | Decision (2026-05-24): Workflow YAML tool with two chained `http` steps calling OpenAlex REST API directly (not ES\|QL — Jeremy confirmed OpenAlex data is NOT indexed in ES; OpenAlex is on-demand only because the 200M-work corpus is unsuitable for preloading). Workflow `openalex-citing-works` registered to Kibana via new `upsert_workflow.sh`; tool wrapper `openalex_citing_works` registered via existing `upsert_tool.sh`. Tool surfaces through Elastic MCP server alongside the three drift tools. | Phase 4a-2 `upsert_tool.sh`; Phase 4a workflow YAML templates; new `upsert_workflow.sh` covering the gap that 4a workflows had to be UI-pasted. | 3-stage Spike captured the `http` step output envelope shape (`{status, statusText, headers, data:<parsed JSON>}` — NOT `.body`, NOT json_parse) and the LiquidJS filter capability surface. See Phase 5d changelog for details. |
| **5e. `citation_finder` MCP-ify + deploy** | Once 5d's tool exists, point `citation_finder` at it via `McpToolset(tool_filter=["openalex_citing_works"])`. Inline + deploy. Verify it stops fabricating DOIs (§3.3 invariant). Smoke test MUST use a **real preprint DOI** (from `preprints` index where `record_source != "demo_seed"` — i.e. a Jeremy puller-ingested record). Demo seed DOIs are synthetic and OpenAlex returns 404 for them; smoke-testing with a demo DOI cannot validate the agent's happy path. This is the first agent in the chain that crosses the system boundary to a real external API, so the "use real ingested data" requirement starts here. | Same as 5a | Smoke-test on real OpenAlex DOIs (e.g. a recent biorxiv ingestion); verify `affected_citations[].citing_paper_doi` populated from OpenAlex, no fabricated `10.0000/synthetic-v0-*` sentinels. |
| **5f. Main-flow orchestration (inverted topology — see §9.6.1)** | (i) Build `supervisor_agent` ADK agent on Agent Engine that encodes §4.1 fan-out: claim_extractor (×2) → drift_analyzer → citation_finder → fan-out notifier per affected citation. (ii) Build minimal Cloud Run dispatcher (~30 lines Python) that receives webhook from Elastic, mints GCP access token from its own service-account identity, calls supervisor via `reasoningEngines:streamQuery`. (iii) Elastic Workflow (scheduled, e.g. every 5 min) runs an ES\|QL query for new `published_doi` rows in `preprints` and `http.request`-POSTs them to the dispatcher with a self-managed bearer token. | Phase 4a Workflow YAML authoring pattern; existing `_shared/config.py` model constants; Phase 4b-5/6 deploy machinery for the supervisor | New: supervisor agent ADK code (compose the 4 sub-agent invocations; no new LLM logic — INSTRUCTIONs reused as-is from each sub-agent's existing definition); Cloud Run dispatcher service; scheduled Elastic Workflow YAML. |
| **5g. Async side-flow (§4.2)** | After `drift_events` write, fire `memory_synthesizer` async. Cheapest implementation: supervisor agent calls `memory_synthesizer` directly as the last step of its fan-out (same Agent Engine, no extra orchestration surface). Alternative: separate Elastic Workflow watching `drift_events` index posting to a second dispatcher endpoint. | 5f's supervisor + dispatcher | If chosen as a separate workflow: one more ES\|QL trigger + one more dispatcher route. Both options are small once 5f is done. |

**What Phase 5 deliberately does NOT do**:

- Doesn't add new business logic to any agent — INSTRUCTIONs and §3 schemas stay frozen.
- Doesn't promote `update_drift_pattern` past append-evidence-only (still deferred per §3.5.1 post-v0 TODO).
- Doesn't deploy the pullers to Cloud Run (separate B-side work; tracked in §5.2 status block). Demo can run on `seed_demo_to_es.py` data; whether the demo narrates "live pull" vs. "seeded" depends on whether Jeremy's pullers are scheduled by demo recording day.

### 9.6.1 Orchestration topology decision (5f shape)

The initial 5f plan said "wire §4.1 as one top-level Elastic Workflow" and flagged "may not natively support call Agent Engine agent". Subsequent research (notes captured in 2026-05-23 changelog) resolved the question: **Elastic Workflows cannot natively invoke a Vertex AI Agent Engine reasoning engine**. The relevant evidence:

1. Elastic Workflows ships an [`ai.agent` step](https://www.elastic.co/docs/explore-analyze/ai-features/agent-builder/agents-and-workflows), but it only invokes agents **registered in Elastic Agent Builder** — it has no knowledge of Vertex AI resources.
2. The fallback — using a generic `http` step to POST to `reasoningEngines:streamQuery` — works on the wire, but Elastic Workflows has **no centralized secret store**. A GCP OAuth2 access token is 1 hour-lived, so the workflow YAML would have to include a "token refresh" step calling Google's token endpoint with a service-account JWT, then pipe the result through Liquid interpolation into the next step's `Authorization` header. Doable but fragile, hard to test, and obscures the agentic logic inside YAML templating.

**Decision**: invert the orchestration topology. Make the **ADK supervisor agent on Agent Engine the orchestrator**; use **Elastic Workflows as the scheduled trigger source** (its strength) and continue to use the Elastic MCP server as the tool surface (already proven). Concrete shape:

```
Elastic Scheduled Workflow (every 5 min)
   - ES|QL: find new `published_doi` rows in `preprints` since last_seen_ts
   - http.request → POST {dispatcher_url} with payload {published_doi, preprint_doi}
   - Authorization: Bearer <static token we mint, stored in workflow YAML>
        │
        ▼
Cloud Run "dispatcher" service (~30 lines Python)
   - Validates bearer token
   - Uses its own service-account identity to mint a GCP access token (free, no refresh dance)
   - POSTs to https://us-central1-aiplatform.googleapis.com/v1/projects/.../reasoningEngines/{supervisor_id}:streamQuery
        │
        ▼
Vertex AI Agent Engine — supervisor_agent (ADK)
   - Encodes §4.1 fan-out in ADK orchestration code (not LLM reasoning)
   - Sequential: claim_extractor(preprint) → claim_extractor(published) → drift_analyzer → citation_finder → fan-out notifier per citation
   - Each sub-agent already deployed (Phase 5a-5e); supervisor calls them as Agent Engine sub-invocations
   - (Optional 5g) → memory_synthesizer as final async step
```

**Why this beats the in-YAML-Workflow approach on three axes** (judged against hackathon evaluation criteria in §9.1):

| Axis | YAML-orchestrator (rejected) | ADK-supervisor (chosen) |
|---|---|---|
| **Rules fit** (Stage One screen: "reasonably apply both Partner and Google products") | Agentic logic in Elastic YAML; Agent Builder used only as managed Gemini runtime — narrow read of "Agent Builder" | Agentic logic in ADK on Agent Builder; Elastic used for storage + MCP tools + trigger — both partners doing what they're best at |
| **Demo recordability** (Stage Two: Design / Impact) | Demo shows YAML steps ticking through — looks like a CI pipeline, not an agent system | Demo shows ADK supervisor trace in Vertex Playground fan-out across sub-agents — visibly "agentic" |
| **Implementation risk** (Stage Two: Technological Implementation) | OAuth2-token-in-YAML refresh dance; Liquid interpolation of bearer tokens; no central secret store | Cloud Run service-account auth is GCP-idiomatic; dispatcher is ~30 lines; supervisor is straight ADK orchestration code |

**What does NOT change about §9.6**:
- 5a–5e (sub-agent deploys + the OpenAlex MCP tool) are unaffected — each sub-agent still needs to be on Agent Engine and call its tools via Elastic MCP, exactly as planned.
- §9.3 / §9.4 tool and agent migration tables are unaffected — Elastic MCP server stays the single tool surface for all 5 agents.
- The §4.1 business flow (which agents run in what order with what payloads) is unchanged — only the *orchestration substrate* differs.

**Sources**:
- [Elastic Workflows — Agent Builder agents and workflows](https://www.elastic.co/docs/explore-analyze/ai-features/agent-builder/agents-and-workflows)
- [Elastic Workflows — External systems & apps steps](https://www.elastic.co/docs/explore-analyze/workflows/steps/external-systems-apps)
- [Vertex AI reasoningEngines.streamQuery REST reference](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1/projects.locations.reasoningEngines/streamQuery)
- [Host AI agents on Cloud Run](https://docs.cloud.google.com/run/docs/ai-agents)

**Implications for D (frontend)**:

- Phase 5 is required before the §4.1 end-to-end timeline view can show real activity. Until 5f lands, the frontend can show static history (querying ES indices directly) but cannot drive a live pipeline from a preprint to an email.
- Memory-loop visualization (the §6.1 highlight event `agent.pattern_retrieved`) can be built against the live `memory_synthesizer` deployment NOW — does not block on Phase 5.
- §6.1 SSE adapter remains unbuilt; if the demo needs real-time agent trace events in the UI, the adapter is its own work item separate from Phase 5.

**Implications for the demo video**:

- Should not record until 5a–5f done (5g optional for the demo but trivial once 5f works).
- Pullers (B) on Cloud Run is independent — affects narration ("system continuously monitors arXiv" vs. "we seeded these 2 demo events") but not the recordability of the agent chain itself.


## Changelog

- 2026-05-20 [Jiayu Zhu] [§1-§8] v0 created.
- 2026-05-21 [Jiayu Zhu] Model picks finalized: `gemini-2.5-flash` (claim_extractor / notifier / citation_finder) + `gemini-2.5-pro` (drift_analyzer / memory_synthesizer). `gemini-3.5-flash` deferred (not reachable via ADK in us-central1).
- 2026-05-21 [Jiayu Zhu] v0 agent scaffolding done; all 5 agents pass `adk web` smoke; v0-finding NOTEs added inline at §3.x for prompt iteration items. Citation Finder fabricates DOIs in v0 — output not persisted until openalex_puller wires up.
- 2026-05-22 [Jiayu Zhu] [§2.2.1-6, §2.3] documented `record_source` tagging — demo records carry `record_source="demo_seed"` injected by `seed_demo_to_es.py`; real data leaves it unset; real-data views filter via `must_not term`.
- 2026-05-22 [Jiayu Zhu] [§3.5.1] retired the `similarity_score >= 0.75` rule (RRF unusable as fixed cutoff at our index size); replaced with LLM-rerank + two function tools (`create_drift_pattern` / `update_drift_pattern`). Memory Synthesizer now self-retrieves. v0 update path is append-evidence-only.
- 2026-05-22 [Jiayu Zhu] **Phase 3 complete — memory loop closed end-to-end.** Both read (Drift Analyzer + `search_drift_patterns`) and write (Memory Synthesizer + create/update) sides verified against live cluster. Tools at `agents/_shared/elastic_{retrieval,write}.py`.
- 2026-05-23 [Jiayu Zhu] [§9 new] Phase 4 deployment shape: ADK on Vertex AI Agent Engine + Elastic Agent Builder MCP server; Python tools migrate to one ES|QL + two Workflow YAML tools.
- 2026-05-23 [Jiayu Zhu] **Finding — `pattern_description` is `semantic_text` → all queries (BM25 / ELSER / match) go through ELSER inference; v0 RRF was ELSER fused with ELSER (two identical sub-rankings).** Simplified to single-MATCH ES|QL for `search_drift_patterns`.
- 2026-05-23 [Jiayu Zhu] **Phase 4a complete — 3 MCP tools live on cluster.** `search_drift_patterns` (ES|QL), `create_drift_pattern` + `update_drift_pattern` (Workflow YAML). All §3.5.1 invariants verified via `_execute`. Three findings: (1) Kibana auto-slugs workflow `name:` snake_case → kebab-case for `id` (use kebab in `configuration.workflow_id`); (2) `_execute` body is `{tool_id, tool_params}`, not `params`; (3) Workflow YAML `{{ }}` stringifies non-string types — use `${{ }}` to preserve array / object / number / bool.
- 2026-05-23 [Jiayu Zhu] **Phase 4b complete — `memory_synthesizer` deployed to Agent Engine (`8580327609152307200`); MCP transport (`POST $KIBANA_URL/api/agent_builder/mcp`) wired via `McpToolset` with `tool_filter` restricting to our 3 tools.** Behavioral identity with Phase 3 confirmed. Deploy patterns now in `agents/_DEPLOY_CHECKLIST.md`: inline `_shared/` constants per-deploy (no cross-deploy `--extra_packages`); hand-author `requirements.txt` instead of `uv export` (avoid local-dev deps); `.env.deploy` must include `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` + `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` (not implied by `--trace_to_cloud`).
- 2026-05-23 [Jiayu Zhu] **Seed bug fix:** `drift_patterns.json` had `support_count: 4` but `source_event_ids` length 2 — violates §3.5.1 invariant `support_count == len(source_event_ids)`. Corrected to 2.
- 2026-05-23 [Jiayu Zhu] **Scope re-evaluation:** 4/5 agents local-only at this point. §1.1 / §4.1 promise 5-agent system; Devpost Stage One needs multi-product breadth. Phase 5 added (§9.6): deploy remaining 4 agents + orchestration.
- 2026-05-23 [Jiayu Zhu] **§9.6 5f shape resolved (§9.6.1 new):** inverted topology — ADK supervisor on Agent Engine drives fan-out; Elastic Workflow only schedules + http-posts to a tiny Cloud Run dispatcher. Elastic Workflows' `ai.agent` step doesn't reach Agent Engine; in-YAML OAuth2 refresh is fragile; inverted topology is GCP-idiomatic + demo-friendly.
- 2026-05-23 [Jiayu Zhu] **Phase 5a / 5b / 5c complete — `drift_analyzer` (`5333654490283245568`), `notifier` (`7063036747193516032`), `claim_extractor` (`2286406392413683712`) on Agent Engine.** Two generalizable findings: (1) any `*_at` wall-clock field belongs to orchestrator, not LLM — LLM anchors to context / training date (Phase 4b-6 `now_iso`; 5b `drafted_at`; 5c `extracted_at` all hit same failure mode); (2) prompt-level JSON-type enforcement saves a redeploy round when downstream agent does math on the field (5c found `value: "45"` string broke drift_analyzer division; preemptive guard in 5a worked first-shot).
- 2026-05-24 [Jiayu Zhu] **Phase 5d complete — `openalex_citing_works` Workflow YAML tool live.** Chose Workflow YAML over inline Python FunctionTool for architectural consistency (5/5 agents route through Elastic MCP). 3-stage Spike methodology kept: spike 1 = workflow + tool + http + auth; spike 2 = `http` step output shape (`steps.X.output.data`, body is `data` not `body`, auto-parsed); spike 3 = chained step ref + real DOI. Findings: workflow YAML input `type` is `string | number | boolean | choice | array` (NOT `integer`); LiquidJS `cgi_escape` non-functional in this Workflows build (OpenAlex tolerates raw `doi.org/` segment).
- 2026-05-24 [Jiayu Zhu] **Phase 5e complete — `citation_finder` (`6997171602643222528`) MCP-ified.** 5/5 agents on Agent Engine. v0 DOI fabrication retired. Three findings: (1) severity judged from citing paper TITLE + drift_summary, NOT citation context (project §1.3 doesn't fetch PDFs; OpenAlex `referenced_works_contexts` not included; industry-wide OA limitation); (2) field-mapping invariants verified — `citing_paper_doi` strips `https://doi.org/`, `orcid` strips `https://orcid.org/`, `citation_context: null` always, `found_at: null` (orchestrator fills); (3) Vertex Playground UI mojibake on non-ASCII (`Nicolás → NicolÃ¡s`) is a renderer bug — SDK returns clean UTF-8; dispatcher / BFF / frontend won't see it.
- 2026-05-24 [Jiayu Zhu] **Phase 5f kickoff — three findings.** (1) ADK has no `RemoteAgent` wrapper to make a deployed reasoning engine a `SequentialAgent.sub_agents` member; supervisor must be a custom `BaseAgent` calling `agent_engines.get(id).async_stream_query(...)` per sub-agent (~150 lines). (2) `ParallelAgent.sub_agents` is fixed at construction time → notifier per-citation fan-out (N unknown at compile time) needs custom BaseAgent reading `ctx.session.state` and merging dynamically. (3) Dispatcher size in §9.6.1 sketch (~30 lines) was an underestimate; actual ~200-250 lines after ES bulk-writes + Gmail OAuth + stream parser. Work split: C takes supervisor + capture script + workflow; A (later C-as-A) takes dispatcher.
- 2026-05-24 [Jiayu Zhu] **Phase 5f-i + 5g complete — supervisor (`7816826734824652800`) deployed; main flow + memory loop run end-to-end against canonical HCQ demo envelope (N=0 affected_citations case).** Three findings: (1) cross-reasoning-engine calls need explicit `roles/aiplatform.user` grant on the Agent Engine service agent (`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) — project-level binding, runtime-effective, no redeploy needed; (2) `_extract_final_output` must handle markdown code fences (Gemini wraps "JSON only" output in ` ```json ... ``` `); supervisor's helper now has 2-stage fallback; (3) sub-agents take JSON-stringified envelopes as `message:` (no kwargs path for structured input in `AdkApp.async_stream_query`).
- 2026-05-25 [Jiayu Zhu] **Phase 5f-ii complete — dispatcher Cloud Run service `claimdrift-dispatcher` (`https://claimdrift-dispatcher-3gz4czm2hq-uc.a.run.app`) live; Track 2 done end-to-end on stub + Cloud Run N=0; Gmail OAuth + send path locally verified (msg_id `19e5d83d50f71419`).** With A unreachable, C swapped in to drive Track 2. Findings in `apps/dispatcher/main.py` + `agents/_DEPLOY_CHECKLIST.md`:
  - **Schema drift across 3 places (mapping JSON, live cluster, demo seed):** §2.2.3 declared `retrieved_patterns_used: keyword | null` but `elastic/mappings/drift_events.json` had stale `retrieved_patterns: nested {...}` from v0 era; live cluster + demo seed JSON mirrored the stale shape; `analyzed_at` was missing entirely. Fix: updated mapping file, DELETE + PUT live index, rewrote demo seed JSON, re-ran `seed_demo_to_es.py`. Same lag found in `notification_log.json` (missing `reasoning_trace`, `drafted_at`; added). Mapping changes must always batch (a) JSON file, (b) live cluster, (c) demo seed JSON, (d) changelog.
  - **Stream extractor differs between supervisor and dispatcher:** supervisor sees ADK `Event` objects (SDK unwrapped function_response → business output); dispatcher sees raw event dicts with MCP wrapper `{"content":[...], "isError":false}` still on `function_response.response`. Dispatcher must use TEXT-PART-ONLY path (markdown fence strip → `json.loads`); first-cut copy of supervisor's 2-stage helper would have silently returned MCP wrappers as drift_events. Same input stream, different parser.
  - **`preprints` docid is composite `{normalized_doi}::{version}`**, not raw DOI. ONBOARDING.md §4 Step 2 says `es.get(id=req.preprint_doi)` — wrong. Dispatcher does `term: {doi: X}` + `sort: [is_final_preprint desc, posted_date desc]` + `size: 1` to pick the right version. ONBOARDING.md needs a doc-only patch (followup TODO).
  - **Cloud Run deploy gotchas:** (a) Compute Engine default SA in new-ish projects no longer has Editor — must explicitly grant `roles/cloudbuild.builds.builder` + `roles/logging.logWriter` + `roles/secretmanager.secretAccessor` + `roles/aiplatform.user` to `<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`; (b) `/healthz` is GFE-reserved (and `/_ah/*`) — intercepted before container, returns Google HTML 404 not FastAPI JSON. Renamed to `/health`; (c) `urls` annotation array contains both old-style `*.us-central1.run.app` and new `*-uc.a.run.app` formats — only the second works; `gcloud run services describe --format='value(status.url)'` returns the working one; (d) CRLF in `.env` flows into `os.environ[]` and silently breaks bearer comparison + makes uvicorn 400 the request before FastAPI sees it (`uvicorn --reload` reloads code but not env vars).
- 2026-05-25 [Jiayu Zhu] **Step 8 (real N>0 e2e) BLOCKED on B's puller.** Cloud Run dispatcher + supervisor + ES + Gmail OAuth all verified independently; the last unverified path is N>0 on real data (real preprint DOI with OpenAlex citing works + real `published_doi` for the pair). Current `preprints` index has 3 real preprints (none has `published_doi` filled — puller doesn't crosswalk to published yet). Once B's puller ingests one real (preprint, published) pair, run one dispatch against it and check: (a) `notification_log` rows for each affected_citation flip `status: drafted -> sent`; (b) emails land in `claimdriftnotifier@gmail.com` (the `DEMO_FALLBACK_EMAIL` configured in the deploy); (c) no errors in Cloud Run logs.
- 2026-05-26 [Jiayu Zhu] **T1 — real N>0 e2e CLOSED.** Test pair: `10.1101/2024.05.03.24306688` v2 → `10.1007/978-3-031-66535-6_19` (4 OpenAlex citing works). 5 rounds, 4 bugs:
  - **Bug 1 — supervisor `_extract_final_output` returned MCP wrapper for tool-using sub-agents** (citation_finder, memory_synthesizer). `function_response` reverse-scan grabbed `{"content":[...],"isError":false}` instead of the final text part. Fix: text-part-only path, mirroring dispatcher `main.py:_extract_from_events`. Missed earlier because Phase 5f-i smoke-tested N=0 (no MCP round-trip).
  - **Bug 2 — supervisor didn't mint `event_id` before fan-out.** drift_analyzer leaves it null per §3.2.2 ("orchestrator fills"); supervisor's notifier envelope at `agent.py:283-285` produced `affected_citation_id="None::<doi>"`. Fix: mint `uuid4()` right after `_extract_final_output(drift_events)`. §3.2.2 example updated: `"event_id": "uuid-v4-here"` → `null`.
  - **Bug 3 — `DEMO_FALLBACK_EMAIL` env var missing on Cloud Run.** citation_finder v0 returns null author emails (§3.3 NOTE); without the fallback, `send_and_update` took the `skipped` branch for all 4. Fix: `gcloud run services update --update-env-vars`.
  - **Bug 4 — two distinct UUIDs per dispatch.** Supervisor minted UUID for fan-out envelopes (Bug-2 fix) but never republished to stream; dispatcher parsed drift_analyzer's text (event_id=null) and minted ITS own at `main.py:151`, so `drift_events._id ≠ affected_citations.drift_event_id`. Fix: supervisor yields one `author=supervisor_agent` Event carrying the post-mint drift_event JSON; dispatcher's `extract_final_output("supervisor_agent")` is now the primary source, drift_analyzer is fallback. Initial fix attempt forgot ADK `Event.invocation_id` is required (Pydantic ValidationError silently closed the async generator → 6 events instead of 17, supervisor stranded after drift_analyzer); corrected by passing `ctx.invocation_id`.
  - **503 noise (open follow-up)** — round 5 first attempt hit `grpc UNAVAILABLE` ~10min in; clean retry. Dispatcher silently aborts on 503. TODO: retry-with-backoff in `get_supervisor_stream`, or write `notification_log.status=failed` for visibility.
  - **Round-5-retry green** (`drift_event_id 12636161-dccf-4be2-8c23-71c3aedf8cbe`): cross-table UUID consistent across all 4 fields; 4 affected_citations (real OpenAlex DOIs); 4 notification_log rows with `status: sent`; 4 Gmail messages to `claimdriftnotifier@gmail.com`.
  - **Spike tooling + golden** checked in: `apps/dispatcher/scripts/{replay_supervisor_stream,analyze_stream,verify_fix,inspect_*}.py` + `tests/golden/{stream_amblyopia_v2.jsonl, t1_drift_event.json, t1_affected_citations.json, t1_notification_log.json}`.
- 2026-05-26 [Jiayu Zhu] **T2 — scheduled trigger workflow live; pipeline self-driving.** Closes the §9.6.1 inverted-topology gap.
  - **New `dispatch_state` index (§2.2.7)** — watermark row `flow_name="main_flow".last_seen_ingested_at`. Mapping JSON + initial row; `refresh_interval` clamped to 5s (Serverless rejects sub-5s). `audit_schema_drift.py` extended with `has_demo_seed=False` for operational-state indices.
  - **dispatcher idempotency gate** — `_find_existing_drift_event(preprint_doi, published_doi)` runs before fan-off; existing pair returns 200 `already_processed` instead of 202. `?force=true` for explicit re-runs. Required because a 5min cadence races a ~200s pipeline; without it, a re-fire piles duplicate drift_events. (5 T1 debug duplicates remain in ES; changelog entries reference their UUIDs so not cleaned up.)
  - **`elastic/agent_builder/workflows/dispatch_new_pairs.template.yaml`** — scheduled `every: 5m`. (1) GET watermark; (2) `elasticsearch.search` filtered `published_doi exists AND is_final_preprint AND ingested_at > watermark AND record_source != demo_seed AND version != published`, sort `ingested_at` DESC, `size: 20`; (3) `foreach` hit → http POST dispatcher; (4) PUT new watermark = `hits[0]._source.ingested_at`, guarded by `if: "...hits.total.value > 0"`. Live + enabled. Noop path verified; fan-out + watermark-write paths deferred (will validate naturally on next ingestion tick producing fresh pairs).
  - **Bearer secret — split-file workaround** (Elastic Workflows 9.3 has no secret store, `consts` are plaintext): `.template.yaml` in git with `<WF_BEARER_TOKEN>` placeholder, `.yaml` gitignored. `upsert_workflow.sh` ships the latter. Investigated GCP-OIDC alternatives: workflow YAML can't sign GCP SA JWTs (no KMS hook); Kibana webhook connector OAuth2 only supports `client_credentials`, not `jwt-bearer`; token-broker function still needs an HMAC secret. Migrate when Elastic 9.4's "HTTP connector with full secret support" ships.
  - **Kibana schema-validator pitfalls** found writing the YAML: (i) `${{ }}` type-preserving interpolation rejected in `size:` ("Expected string | __schema67") — use literal int; (ii) `term: { field: <literal> }` short-form flagged as match-query syntax — use long-form `term: { field: { value: <lit> } }`; (iii) `.last` array chain fails `variable-validation` — use bracket index `[0]` with DESC sort.
- 2026-05-26 [Jiayu Zhu] **T7 — schema-drift audit script + all 7 indices clean.** New [elastic/scripts/audit_schema_drift.py](../elastic/scripts/audit_schema_drift.py) does the three-way check (mapping JSON ↔ live cluster ↔ demo seed JSON) the 2026-05-25 entry warned about. Read-only, stdlib-only, non-zero exit on drift. Verdict: mapping_fields == live_fields for all 7; seed keys are proper subset of mapping (§2.3 `dynamic: strict` invariant); `record_source` present in mapping + live for all 6 business indices.
- 2026-05-26 [Jiayu Zhu] **Scope reduction — arxiv dropped.** §5.1 `arxiv_puller` removed; OAI-PMH XML complexity unjustified given bioRxiv + medRxiv coverage. Cleaned §1.1 / §1.2 / §2.2.1 / §5.1 / §5.2 + `ingestion/run_pull.py` `--preprint-source` / `--batch-source` choice lists. Pre-2026-05-26 changelog entries kept (historical state).
- 2026-05-26 [Jiayu Zhu] **Dispatcher ONBOARDING.md deleted.** Build-time runbook turned drift-prone after dispatcher shipped (2026-05-25 entry's `es.get(id=req.preprint_doi)` doc bug confirmed it was stale). [apps/dispatcher/README.md](../apps/dispatcher/README.md) + `main.py` docstring + §9.6.1 are now the dispatcher's authoritative trail.
- 2026-05-26 [Jiayu Zhu] **Repo hygiene — `.gitignore` `scripts/` catch-all removed.** Was silently excluding `apps/dispatcher/scripts/*` (T1 spike), `elastic/scripts/audit_schema_drift.py` (T7), `elastic/agent_builder/scripts/upsert_workflow.sh` (Phase 5d). `apps/dispatcher/.gitignore` already covers OAuth secrets so removing the global rule doesn't widen secret exposure.
- 2026-05-26 [Jiayu Zhu] **T6 — top-level README rewritten; `agents/README.md` un-staled.** Removed WIP disclaimer + broken `workflows/` link + incorrect Cloud Run / Vercel claims. New structure: elevator pitch + ASCII architecture diagram + deployment-state table + reproduce-from-clean-clone 9-step sequence + T1 reference smoke test. `agents/README.md` v0 status + WIP Cloud Run sections replaced with current 6-reasoningEngine deploy table. `frontend/README.md` added (D pending; pointer to BFF / §6.1 contract).
