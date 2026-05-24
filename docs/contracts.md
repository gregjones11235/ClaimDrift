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
| **Ingestion Pipeline** (non-agent) | Pulls data from arXiv/bioRxiv/medRxiv/Crossref/OpenAlex, writes to `preprints` index |
| **Claim Extractor** | Decomposes each preprint version into structured claims, writes to `claims` |  //gemini-2.5-flash
| **Drift Analyzer** | Diffs claim sets between v-final-preprint ↔ published, produces drift report, writes to `drift_events` |  //gemini-2.5-pro
| **Citation Finder** | Finds downstream papers citing the drifted preprint, scores severity, writes to `affected_citations` |  //gemini-2.5-flash
| **Notifier** | Drafts and sends (to test inbox) an email per affected citation, writes to `notification_log` |  //gemini-2.5-flash
| **Memory Synthesizer** | Distills drift events into reusable patterns, writes to `drift_patterns` |  //gemini-2.5-pro

### 1.2 Data flow

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

**TODO D (Day 5-7)**: While building the frontend, if you find that some index isn't needed, or you need a new aggregated view (e.g., affected_citations grouped by author), or fields are insufficient — ping B and C on chat.

### 2.2 Mapping details

B fills in the mapping for each index. Below are the minimum field constraints — B adds analyzers, index options, and the ELSER semantic hookup on top.

#### 2.2.1 `preprints` index

**Minimum fields** (B must include):

- `record_source`: keyword | null (`"demo_seed"` for records written by `elastic/scripts/seed_demo_to_es.py`; real puller-ingested docs leave it unset; see §2.3)
- `doi`: keyword
- `source`: keyword (`arxiv` | `biorxiv` | `medrxiv`)
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
  "event_id": "uuid-v4-here",
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
| `arxiv_puller` | arXiv OAI-PMH | Cloud Scheduler, daily | `preprints` |
| `biorxiv_puller` | bioRxiv REST | Cloud Scheduler, hourly | `preprints` |
| `medrxiv_puller` | medRxiv REST | Cloud Scheduler, hourly | `preprints` |
| `crossref_puller` | Crossref Event Data webhook + REST fallback | Webhook + Cloud Scheduler as backup | updates `published_doi` field of `preprints` |
| `openalex_puller` | OpenAlex REST | on demand (triggered by Citation Finder) | (does not write ES directly; returns to agent) |

### 5.2 General rules (B must follow)

- All outbound requests carry polite-pool headers (`User-Agent` includes contact email)
- arXiv rate limit ≥3 seconds/request
- Use bulk API for ES writes
- DOI normalization: lowercase, no `https://doi.org/` prefix
- Writes are upsert (existing DOI is updated)

**Status (2026-05-23)**: code-complete locally (bioRxiv / medRxiv / Crossref / OpenAlex pullers + bulk-upsert path) — see `ingestion/README.md` + `ingestion/run_pull.py`. Dry-run mode verified. **Open**: Cloud Run Job deployment + Cloud Scheduler trigger wiring not yet done; `preprints` index is currently populated by `seed_demo_to_es.py`, not by scheduled pulls. Affects 4c demo narrative (real-time vs. seeded data) — see Open Questions.

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

- 2026-05-20 [Jiayu Zhu] [§1-§8] v0 created
- 2026-05-21 [Jiayu Zhu] determine model types
- 2026-05-21 [Jiayu Zhu] v0 agent scaffolding complete (ADK + Vertex AI in us-central1):
  - All 5 agents pass smoke tests via `adk web`; see `agents/README.md` v0 status
  - `gemini-3.5-flash` deferred (not yet reachable via ADK in us-central1); using `gemini-2.5-flash` / `gemini-2.5-pro` for v0
  - §3.1.2: replaced placeholder model id `"gemini-2.x-..."` with current `"gemini-2.5-flash"`
  - §3.1.2 / §3.2.2 / §3.3.2 / §3.4 / §3.5: added v0-finding NOTEs for prompt-iteration items (A to address); see `agents/README.md` for issue tracker
  - §3.2.2: tentative proposal — add `scope_restricted` to `diff_type` enum (pending team discussion per §8.1)
  - §3.3: v0 Citation Finder fabricates DOIs; output must not be persisted until openalex_puller is wired
- 2026-05-22 [Jiayu Zhu] [§2.2.1-§2.2.6, §2.3] documented `record_source` field (added to all 6 index mappings by Jeremy in the B-side port; tagging strategy via `seed_demo_to_es.py` rather than seed JSON files; real-data views filter demo records via `must_not term`)
- 2026-05-22 [Jiayu Zhu] [§3.5.1] retired the `similarity_score >= 0.75` create-vs-update rule (RRF score not usable as a fixed cutoff in our index size; verified via `probe_rrf_scores.py`); replaced with LLM-judged per-candidate decision routed through two function tools `create_drift_pattern` / `update_drift_pattern` (Memory Synthesizer now self-retrieves rather than being fed `existing_similar_patterns` by the orchestrator). v0 `update_drift_pattern` is append-evidence-only; description / domain_tags refinement deferred to a post-v0 TODO listed in §3.5.1.
- 2026-05-22 [Jiayu Zhu] **Phase 3 complete — memory loop closed end-to-end.** Read side (Drift Analyzer + `search_drift_patterns`) and write side (Memory Synthesizer + `create_drift_pattern` / `update_drift_pattern`) both verified against the live cluster:
  - Smoke tests: create (UUID v4 minted, support_count=1), update (append + dedup, support_count=2 after two distinct event_ids, stable at 2 on a repeat), 404 path (unknown pattern_id raises loudly rather than silent-fallback to create), DELETE confirmed. See `agents/_shared/elastic_write.py`.
  - End-to-end agent run: fed the hydroxychloroquine drift_event (the same payload that validated Drift Analyzer in Phase 2) into `memory_synthesizer` via `adk web`. Tool trace: 1× `search_drift_patterns(top_k=5, no min_score)` → 1× `update_drift_pattern(pattern_id="pattern-demo-001", source_event_id=...)`. No `create_drift_pattern` call (correctly chose update over create since the COVID effect-size-reduction phenomenon already had a matching pattern). Output `action="update_existing"`, returned pattern has `support_count: 3`, `source_event_ids` correctly appended, `last_updated_at` is now-time, `created_at` preserved.
  - LLM rerank held: read each candidate's `pattern_description` to judge "same phenomenon?" — did not rely on RRF score (confirms the Phase 1 finding that RRF is unusable as a fixed threshold).
  - v0 append-only update constraint held: agent did NOT attempt to refine `pattern_description` or extend `domain_tags` during update (deferred per the §3.5.1 post-v0 TODO).
  - Cleanup: re-seeded `pattern-demo-001` from `elastic/demo_seed/drift_patterns.json` via `seed_demo_to_es.py` to restore demo state.
- 2026-05-23 [Jiayu Zhu] [§9 new] documented the Phase 4 deployment architecture after auditing the hackathon rules. Three constraints determine the shape: (a) Gemini-only at project runtime → keeps us on ADK + Vertex; (b) "must use Agent Builder" → satisfied by deploying ADK agents to Vertex AI Agent Engine (the Agent Builder platform's managed ADK runtime), so Phases 1-3 code is reusable as-is; (c) Elastic track must use the Partner's MCP server → we will NOT self-write an MCP server. The three Phase-3 Python tools (`search_drift_patterns`, `create_drift_pattern`, `update_drift_pattern`) will be re-expressed inside Elastic Agent Builder as one ES|QL tool + two Workflow YAML tools, naturally exposed through Elastic's built-in MCP server. Python implementations stay in the repo as the behavioral reference spec / regression harness. Phase 4a (tool migration) begins next; Phase 4b (`adk deploy agent_engine`) follows.
- 2026-05-23 [Jiayu Zhu] **Finding — RRF over `pattern_description` was fusing the same source twice.** During Phase 4a planning, ran three Kibana Dev Tools probes against the live serverless 9.5 cluster: (1) ES|QL `MATCH(pattern_description, "vaccine trial results weakened")`; (2) Search-DSL `semantic { field: pattern_description, query: ... }`; (3) Search-DSL `match { pattern_description: { query: ... } }`. All three returned **byte-identical results** — same documents, same ranks, same `_score` (neuro 10.5949, demo 9.4961, agri 6.2885). Because `pattern_description` is mapped as `semantic_text`, Elasticsearch routes any query on this field through ELSER inference and does not retain a raw-token BM25 index. **Implication for Phase 1's RRF design:** the v0 `retriever.rrf` over BM25 + ELSER, on this field, was ELSER fused with ELSER — two identical sub-rankings. That explains the Phase 1 finding that RRF scores were stuck at a handful of mechanical values (`1/(60+1)+1/(60+1)`, `1/(60+1)+1/(60+2)`, …): with two identical inputs, RRF degenerates to a pure function of rank. The Phase 1 conclusion — "score is unreliable, the LLM must rerank by reading the description" — remains correct (ES|QL `MATCH` scores spread wider but still misrank: in this probe, neuro outranks the truly-relevant demo by `_score`). What changes: §9.3 now specifies a single-`MATCH` ES|QL tool for `search_drift_patterns`; no RRF wrapper is needed. `agents/_shared/elastic_retrieval.py` stays in-repo as v0 reference / regression harness.
- 2026-05-23 [Jiayu Zhu] **Phase 4a-2 complete — `search_drift_patterns` ES|QL tool created and verified on the live cluster.** Tool definition committed at `elastic/agent_builder/tools/search_drift_patterns.json`; idempotent upsert script at `elastic/agent_builder/scripts/upsert_tool.sh` (reads `agents/.env` for `KIBANA_URL` + `ELASTIC_API_KEY`; auto-detects create vs update by checking existence). After fixing one schema mismatch (Agent Builder ES|QL tools reject `params.{name}.default`; removed it — both params are now required), `POST /api/agent_builder/tools` succeeded with `readonly:false`, `experimental:false`. End-to-end verification via `POST /api/agent_builder/tools/_execute` with query "covid clinical effect size reduction with hedging", top_k=3: returned 3 patterns with `pattern-demo-001` ranked top-1 (`_score=32.75`), `probe-neuro-001` second (9.78), `probe-econ-001` third (6.16). Response envelope includes both the parameter-substituted ES|QL string and an `esql_results` data block with columns + row arrays — the shape MCP clients will consume in Phase 4b. Note: the in-repo `agents/_shared/elastic_retrieval.py` Python function is no longer the runtime; it remains as v0 reference / regression harness per §9.5.
- 2026-05-23 [Jiayu Zhu] **Phase 4a-3 complete — `create_drift_pattern` workflow + workflow tool deployed and verified end-to-end.** Workflow YAML at `elastic/agent_builder/workflows/create_drift_pattern.yaml` (single `elasticsearch.request` step: `PUT _doc/{id}?op_type=create`); workflow tool spec at `elastic/agent_builder/tools/create_drift_pattern.json` (`type:"workflow"`, `configuration.workflow_id:"create-drift-pattern"`). Three non-obvious findings worth recording for future tools:
  - **Slug conversion gotcha**: Kibana auto-slugs workflow `name:` from snake_case to kebab-case for the workflow `id`. `name: create_drift_pattern` → `id: "create-drift-pattern"`. Tool spec's `configuration.workflow_id` MUST use the kebab form or the POST returns 400 `Workflow '...' not found`. Found via `GET /api/workflows` which returns the canonical id. Apply the same rule when authoring `update-drift-pattern` and any future workflow tool.
  - **`_execute` endpoint shape**: `POST /api/agent_builder/tools/_execute` with body `{"tool_id": "...", "tool_params": {...}}` — NOT `/tools/{id}/_execute` (returns 404) and NOT `"params"` instead of `"tool_params"` (returns 400). Same envelope for both ES|QL tools and workflow tools.
  - **Schema auto-derivation**: Kibana derives the JSON-schema `params` of a workflow tool from the workflow's `triggers[].inputs` automatically — we do NOT declare `params` in the tool JSON. Returned schema in our case correctly mapped 6 inputs with `required: [...]` populated and `domain_tags` typed as `array` (though with empty `items: {}` since YAML `type: array` has no sub-type).
- 2026-05-23 [Jiayu Zhu] **Phase 4a-4 complete — `update_drift_pattern` workflow + workflow tool deployed and verified end-to-end.** Workflow YAML at `elastic/agent_builder/workflows/update_drift_pattern.yaml` (single `elasticsearch.request` step: `POST _update/{id}` with painless script doing atomic set-union dedup + support_count recompute + last_updated_at refresh). Tool spec at `elastic/agent_builder/tools/update_drift_pattern.json` (`configuration.workflow_id:"update-drift-pattern"`). All three §3.5.1 invariants verified via `_execute`:
  - **Append new event** (`evt-alpha` already in fixture, add `evt-beta`): `output.result:"updated"`, `_version` bumped to 2.
  - **Repeat-event dedup** (re-send `evt-beta`): `output.result:"updated"` (painless touched `last_updated_at`) but post-GET confirms `source_event_ids` stays at length 2 and `support_count=2` — set-union held.
  - **404 fails loudly** (unknown `pattern_id`): `execution.status:"failed"` with `error_message:"document_missing_exception"`. No silent fallback to create — the §3.5.1 contract holds at the Workflows layer just like it did at the Python layer.
  - Post-GET confirms `created_at` preserved (14:00:00Z, set on create), `last_updated_at` refreshed (14:10:00Z), `pattern_description` / `pattern_type` / `domain_tags` untouched (v0 append-evidence-only scope).
- 2026-05-23 [Jiayu Zhu] **Phase 4a-5 (bugfix) — Workflows YAML stringifies non-string interpolations unless you use the Liquid type-preserving syntax `${{ }}`.** During 4a-4 verification, the post-update GET on `smoke-test-update-fixture` revealed `domain_tags` stored as the STRING `"smoke-test"` instead of the ARRAY `["smoke-test"]` that was passed in. Root cause: `create_drift_pattern.yaml` used `domain_tags: "{{ inputs.domain_tags }}"`, and Elastic Workflows' templating engine (Liquid; https://liquidjs.com/) renders bare `{{ }}` as a STRING regardless of input type. The fix is the dollar-prefixed `${{ }}` form documented at https://www.elastic.co/docs/explore-analyze/workflows/templating: *"Use the dollar-sign prefix (`${{ }}`) when you need to preserve the original data type (array, object, number, boolean). The type-preserving syntax must occupy the entire string value."* Patched `create_drift_pattern.yaml:59` to `domain_tags: "${{ inputs.domain_tags }}"`; re-verified with multi-element input `["tag-a", "tag-b", "tag-c"]`: stored as native array. **Generalize**: every future workflow that interpolates an `inputs.X` or `steps.X.output` of type array/object/number/boolean into an `elasticsearch.request` body MUST use `${{ }}`. String inputs are safe with either form. Audit checklist for new workflows added: grep `\{\{[^$]` across workflows/ should match only string-typed interpolations.
- 2026-05-23 [Jiayu Zhu] **Phase 4a complete.** All three tools live on the cluster, each verified end-to-end via the MCP-facing `_execute` endpoint, with all Phase-3 Python-tool behavioral invariants preserved (UUID create collision detection; atomic update dedup + support_count recompute + created_at preservation; 404 raises loudly). Phase 4b (deploying ADK `memory_synthesizer` to Vertex AI Agent Engine and wiring the Elastic MCP server in place of the direct Python imports) is now unblocked. `agents/_shared/elastic_retrieval.py` and `elastic_write.py` remain in-repo as regression harness per §9.5.
- 2026-05-23 [Jiayu Zhu] **Phase 4b-1/2/3 complete — Memory Synthesizer agent rewired to consume tools via Elastic MCP server, end-to-end behavior identical to Phase 3.** `tools/list` ping against `POST $KIBANA_URL/api/agent_builder/mcp` (JSON-RPC 2.0, requires header `Accept: application/json, text/event-stream` — bare `application/json` returns 406) returned 19 tools: our three (`search_drift_patterns`, `create_drift_pattern`, `update_drift_pattern`, names = the `id` we POSTed in 4a) plus 16 built-in `platform.*` tools (`platform.core.execute_esql`, `platform.streams.update_stream`, …) that the MCP server exposes by default. Rewired `agents/memory_synthesizer/agent.py` to use `google.adk.tools.mcp_tool.McpToolset` + `StreamableHTTPConnectionParams` (streamable HTTP, NOT SSE) with `tool_filter=["search_drift_patterns","create_drift_pattern","update_drift_pattern"]` to hide the 16 unrelated built-ins from the LLM (both for §3.5 scope hygiene and to keep the demo's tool trace clean). INSTRUCTION unchanged, model unchanged (`gemini-2.5-pro`), tool names/schemas unchanged. Local `adk web` run with the §3.5.1-shaped envelope for `demo-drift-001` (hydroxychloroquine viral-load 45%→12%, drift_summary verbatim from `elastic/demo_seed/drift_events.json`) produced the expected tool trace: `search_drift_patterns(query_text=drift_summary, top_k=5)` → `update_drift_pattern(pattern_id="pattern-demo-001", source_event_id="demo-drift-001", now_iso=...)` → §3.5.2 envelope with `action:"update_existing"`. Dedup held (`source_event_ids` stayed `["demo-drift-001","demo-drift-002"]` after the rerun on an already-supporting event). `_version` advanced from 3 → 4 across this run. Behavioral identity with Phase 3 confirms transport substitution (in-process Python call → MCP HTTP/JSON-RPC) is transparent to business logic.
- 2026-05-23 [Jiayu Zhu] **Seed inconsistency found and fixed in `elastic/demo_seed/drift_patterns.json`.** During Phase 4b-3 verification, baseline GET of `pattern-demo-001` showed `support_count: 4` while `source_event_ids` had length 2. Root cause: the seed JSON hard-coded `support_count: 4` while the array element count is 2 — these two fields are required by §3.5.1 to satisfy the invariant `support_count == len(source_event_ids)`, and `seed_demo_to_es.py` writes the JSON verbatim without reconciling them. Every `update_drift_pattern` call would silently rewrite `support_count` to 2 via the painless `ctx._source.source_event_ids.size()` line (Phase 4a-4) — which is the correct behavior (the §3.5.1 invariant being enforced), but it created a confusing "support_count decreased after an update" appearance on freshly-seeded demos. Fix: changed the seed file's `support_count` from 4 to 2 (commit alongside this changelog entry). The "right" value of `support_count` is always `len(source_event_ids)`; it is NOT an independent counter, just a materialization of the array's length for cheap sort/filter at retrieval time. **Post-v0 TODO**: if a future demo wants to showcase higher accumulation (e.g. "this pattern has been seen 5 times across COVID/flu/HCV"), extend `source_event_ids` to length 5, do NOT raise `support_count` independently.
- 2026-05-23 [Jiayu Zhu] **Phase 4b-5/6 complete — `memory_synthesizer` deployed to Vertex AI Agent Engine and verified end-to-end against the live Elastic MCP server.** Resource: `projects/tensile-topic-496519-i1/locations/us-central1/reasoningEngines/8580327609152307200`. Two findings from the deploy attempt:
  - **Round 1 failed with HTTP 500 (empty error message)**: `adk deploy agent_engine` staged only `memory_synthesizer/` itself into the deploy tarball, but `agent.py` had `from _shared.config import MODEL_PRO` — the sibling `_shared/` package was not copied. Server-side import error surfaced as a generic 500 (no `_shared` ModuleNotFoundError leaked through to the CLI). Fix: inlined `MODEL_PRO = "gemini-2.5-pro"` directly in `agent.py` with a comment pointing back at `_shared/config.py` for sync (the constant hasn't moved since v0). `_shared/` stays in-repo because other local agents (`drift_analyzer`, `claim_extractor`, etc.) still use it via `adk web`. If future agents are deployed to Agent Engine, each should inline its own constants — do not try to `--extra_packages` the shared module across deploys.
  - **Round 1 also exported a fat `requirements.txt` (~12KB, ~80 transitive deps)** from `uv export --no-hashes --format requirements-txt --no-dev`. This was not the deploy failure cause but it inflated the build container with local-dev-only deps that Agent Engine doesn't need (`aiosqlite` for `adk web` session storage; `alembic`, `authlib`, etc.). Replaced with a hand-authored 2-line `requirements.txt` listing only `google-adk>=1.0,<2.0` and `google-cloud-aiplatform[adk,agent-engines]>=1.112`. Agent Engine's deploy machinery automatically adds `cloudpickle` and `pydantic` on top. Round 2 succeeded.
  - **Round 2 deploy artifacts** (kept in repo): `agents/memory_synthesizer/requirements.txt` (committed; hand-authored), `agents/memory_synthesizer/.env.deploy` (gitignored; contains only `KIBANA_URL` + `ELASTIC_API_KEY`, intentionally stripped of `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` which conflict with `--project`/`--region` flags per `google/adk-python` issue #1185).
  - **E2E verification on the deployed agent**: identical hydroxychloroquine §3.5.1 envelope used in Phase 4b-3 was submitted via the Agent Engine console Playground. Tool trace (visible in Playground) matched local Phase 4b-3 verbatim: `search_drift_patterns(top_k=5)` → `update_drift_pattern(pattern_id="pattern-demo-001", source_event_id="demo-drift-001", now_iso=…)` → §3.5.2 envelope with `action:"update_existing"`, `_version: 5`, `_shards.successful: 1`. Post-GET of `pattern-demo-001` confirmed: `_version: 5` (+1 from 4b-3's leave-state); `source_event_ids: ["demo-drift-001","demo-drift-002"]` (dedup held — `demo-drift-001` was already a supporting event); `support_count: 2`; `created_at: "2026-05-21T04:49:25Z"` preserved across the painless update; `last_updated_at` refreshed; `record_source: "demo_seed"` preserved (painless doesn't touch this field).
  - **Egress to Elastic Cloud Kibana from Agent Engine worked first try, with no special VPC / PSC configuration.** This confirms the research-agent's read of Vertex AI Agent Engine networking docs: "outbound traffic egresses directly from the secure, Google-managed tenant network" in the default (non-VPC-SC) configuration.
  - **Minor v0 imperfection (does not affect business logic)**: the LLM's chosen `now_iso` was `2026-05-21T05:50:00Z` rather than today's wall-clock (`2026-05-23`). It appears the model anchored to a date already in context (likely `created_at` from a retrieved pattern). `last_updated_at` two days off is harmless for the memory loop (it's an ordering hint, not a precision timestamp), but the demo narration should not promise "the timestamp reflects when the agent ran". **Post-v0 prompt tweak**: instruct Memory Synthesizer to use a placeholder like `"NOW"` and have an orchestrator step substitute the real time, mirroring how `synthesized_at` is left `null` per the existing §3.5.2 design.
- 2026-05-23 [Jiayu Zhu] **Phase 4 (memory loop) complete.** All three Phase-3 Python tools migrated to Elastic Agent Builder (one ES|QL tool + two Workflow YAML tools; §9.3); `memory_synthesizer` ADK agent deployed to Vertex AI Agent Engine (resource `8580327609152307200`); MCP transport (`POST $KIBANA_URL/api/agent_builder/mcp`) connects the two with `tool_filter` restricting the LLM to our three drift tools. Every §3.5.1 / §3.5.2 invariant verified end-to-end on the deployed runtime. Hackathon rule compliance (§9.1): (a) Gemini-only at runtime ✓ (`gemini-2.5-pro`); (b) Google Cloud Agent Builder ✓ (Agent Engine is the Agent Builder platform's managed ADK runtime); (c) Partner Entity's MCP server ✓ (Elastic's own MCP server, not a self-written shim). The Phase-3 Python implementations at `agents/_shared/elastic_retrieval.py` and `agents/_shared/elastic_write.py` remain in the repo as regression harness per §9.5. **Initial assessment said "Phase 4c demo video unblocked" — that assessment was corrected within hours by the 2026-05-23 scope re-evaluation entry below; demo is now held pending Phase 5 (§9.6).**
- 2026-05-23 [Jiayu Zhu] **Scope re-evaluation — Phase 4 is necessary but not sufficient for the demo; Phase 5 added (§9.6).** The earlier "Phase 4c demo unblocked" call missed that only 1 of 5 agents is on Agent Engine, while §1.1 / §4.1 promise a 5-agent system. Re-read of Devpost rules shows judges install and test the repo ("for judging and testing"; "must be capable of being successfully installed and run consistently"), and Stage One screen requires "reasonably apply both ... Partner and Google Cloud products" — with 4/5 agents local-only and not touching the partner stack, that screen is at risk. Base rate of recent Google Cloud agent-themed hackathon grand prizes is multi-agent breadth (ADK Hackathon 2025: 4-capability; GKE EMEA: 6 agents); narrow-deep wins exist but are exceptions. **Decision**: hold the demo video and Devpost description until Phase 5 (§9.6) puts the remaining 4 agents on Agent Engine + wires the main-flow orchestration. Phase 5 is mostly mechanical reuse of Phase 4 patterns (`McpToolset` wiring, `adk deploy agent_engine`, `upsert_tool.sh` for any new MCP tool); the one substantive unknown is whether Elastic Workflows YAML can natively invoke an Agent Engine reasoning engine as a step (needed for the §4.1 main flow), or whether the orchestrator step has to be an `http.request` to the Agent Engine REST endpoint — Phase 5f research item. Frontend (D) can start the 60-70% of work that does not depend on the unfinished agents (Tailwind/shadcn layout, TypeScript types from §2.2 / §3.x / §6.1, static views over ES, memory-loop visualization against the already-deployed `memory_synthesizer`); the 30-40% that depends on the full agent chain or the SSE adapter waits for Phase 5 and the BFF adapter respectively.
- 2026-05-23 [Jiayu Zhu] **§9.6 5f shape resolved — inverted orchestration topology adopted (§9.6.1 new).** The 5f "research needed" flag from the prior scope-revaluation entry is now closed. Documentation read confirms two negatives and one positive: (a) Elastic Workflows' `ai.agent` step only invokes Elastic-Agent-Builder-registered agents, NOT Vertex AI Agent Engine reasoning engines; (b) the workflow `http` step can reach `reasoningEngines:streamQuery` on the wire but Elastic Workflows has no centralized secret store, forcing a fragile in-YAML OAuth2 token-refresh dance (1-hour GCP access token lifetime requires a "mint JWT → exchange for access token" pre-step on every run, piped via Liquid into the next step's `Authorization` header); (c) inverted topology (ADK supervisor on Agent Engine drives the fan-out; Elastic Workflow only triggers via scheduled ES|QL + `http.request` to a tiny Cloud Run dispatcher) is GCP-idiomatic, judging-criteria-aligned (keeps agentic logic on Agent Builder, which is the Stage One screen surface), and demo-friendly (Vertex Playground shows supervisor trace fanning out across sub-agents rather than YAML steps ticking through). 5f rewritten as three sub-deliverables: supervisor ADK agent, ~30-line Cloud Run dispatcher, scheduled Elastic Workflow. 5g preferred shape: supervisor calls `memory_synthesizer` as the last step of its own fan-out (no extra orchestration surface); alternative kept as fallback. §9.3 / §9.4 / §4.1 business contracts unchanged — only orchestration substrate differs.
- 2026-05-23 [Jiayu Zhu] **Phase 5b complete — `notifier` deployed to Vertex AI Agent Engine (resource `7063036747193516032`); three deploy-pattern improvements captured for the remaining 4 agents.** E2E smoke-tested via Vertex Playground with the §3.4.1 envelope for `affected_citation_id="demo-drift-001::10.1038/demo.2024.100"` (Jane Doe, central severity, hydroxychloroquine viral-load drift). Output passed all §3.4.2 invariants: structure + 6 top-level fields, `affected_citation_id` echo, `dispatch.status="drafted"`, body 197 words (within 150-300), both preprint and published claim sentences quoted verbatim (the v0 fragment-quoting finding from 2026-05-21 did NOT recur), addressed by name, disclaimer present, neutral non-lecturing tone. Session telemetry visible: 8.6s duration, 1242 tokens, 1 model call, 0 tool calls (expected — notifier has no MCP tools), 0 errors. Three substantive findings now codified in `agents/_DEPLOY_CHECKLIST.md`:
  - **Pitfall #7 (new): `--trace_to_cloud` alone does NOT enable the Agent Observability dashboard / prompt-response content capture.** Source check of `google/adk-python` `src/google/adk/cli/cli_deploy.py`: `--trace_to_cloud` only sets `enable_tracing=True` on the generated `AdkApp` (basic Cloud Trace spans). The Vertex console UI surfaces two separate env vars that must be set via `--env_file`: `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` (runtime OTel export → dashboard + traces page) and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` (prompt/response body capture; off by default per OTel GenAI semconv). Both env vars are independent of each other and of the flag. Phase 4b-5 happened to set both in `memory_synthesizer/.env.deploy` so this was masked. First notifier deploy attempt used only `--trace_to_cloud` → UI nagged to enable the env vars; redeploy with all three knobs set fixed it. Every future agent's `.env.deploy` MUST include both env vars.
  - **`.gitignore` widened from `memory_synthesizer/.env.deploy` to `*/.env.deploy`** to cover every per-agent deploy env file under `agents/`. Verified with `git check-ignore notifier/.env.deploy memory_synthesizer/.env.deploy` (both ignored).
  - **§3.4.2 `drafted_at` schema change**: now `null` in agent output, filled by the §9.6.1 Cloud Run dispatcher on receive. LLMs cannot read a real clock — first smoke test returned `drafted_at: "2024-07-30T12:00:00Z"`, anchored not even to demo data (which is 2026-05-21) but to a training-data prior. This is the third recurrence of the "LLM anchoring to context/training date" failure mode (Phase 4b-6 `now_iso`; this one). Fix is structural, not prompt-iteration: pull machine-clock fields out of agent output and into the orchestrator, mirroring §3.2.2 `analyzed_at` and §3.5.2 `synthesized_at`. Edits: `contracts.md §3.4.2` annotation, `agents/notifier/agent.py` INSTRUCTION explicitly says "leave drafted_at null", redeployed in-place via `--agent_engine_id=7063036747193516032` (no orphan resource). Smoke test 3 confirmed `drafted_at: null`. **Generalize**: any future schema field whose semantic is "wall-clock time of an agent action" should default to null in the agent's contract; orchestrator fills on receive. Dispatcher (5f) will add a one-liner `payload["drafted_at"] = datetime.now(UTC).isoformat()` before writing `notification_log`.
  - **`agents/_DEPLOY_CHECKLIST.md` (new, 5-step + 7 pitfalls + per-agent status table + canonical test envelopes)** is the per-agent runbook for the remaining 3 deploys (5c/5a/5e). Hand-holds Steps 1-5 with the exact `uv run adk deploy agent_engine` invocation shape (including `--agent_engine_id` for in-place re-deploys to avoid orphan resources), the minimum 2-line / 4-line `.env.deploy` templates depending on whether the agent uses MCP, and a status table tracking which agents are deployed and their `reasoningEngines/<id>` values (feed Phase 5f supervisor).
- 2026-05-23 [Jiayu Zhu] **Phase 5c complete — `claim_extractor` deployed to Vertex AI Agent Engine (resource `2286406392413683712`).** Took 2 deploy iterations: first deploy passed structural checks and the `extracted_at: null` invariant (§3.1.2 was tightened at the same time to mirror §3.2.2 / §3.4.2 / §3.5.2 — same wall-clock-field-belongs-to-orchestrator rationale), but `numerical_values[0].value` was returned as the JSON STRING `"45"` instead of the number `45`. That would break `drift_analyzer`'s `relative_delta = (b - a) / a` computation (Python TypeError on string/string division). Second deploy added one line to the INSTRUCTION: "value MUST be a JSON number, not a string. ... Downstream drift_analyzer ... will throw at division time." Smoke-test post-redeploy returned `"value": 45` (number, no quotes). Two cosmetic drifts remain and are routed to A's prompt iteration domain rather than treated as bugs: (1) `unit: "%"` vs the §3.1.2 example `"percent"` — semantically equivalent for the downstream LLM `drift_analyzer`, no normalization needed for v0; (2) `claim_type: "causal"` on a sentence that is both quantitative (45%) and causal (HCQ → load reduction) — §3.1.2 explicitly says "v0 we take only the primary tag" without ordering, so this is a judgment call A may want to tighten. The v0-finding-checkpoint that `comparison: "reduction"` is non-null when reduction verbs are present (§3.1.2 NOTE 2026-05-21) held — both deploys returned `"reduction"` correctly. **Generalize**: prompt-level JSON-type enforcement is cheap and worth doing preemptively for any agent whose output gets math'd downstream. Adding similar guards to `drift_analyzer` (`numerical_delta.preprint_value` / `published_value` etc.) before its deploy will save a redeploy round.
- 2026-05-23 [Jiayu Zhu] **Phase 5a complete — `drift_analyzer` MCP-ified and deployed to Vertex AI Agent Engine (resource `5333654490283245568`).** First-shot deploy passed all §3.2.2 invariants on the hydroxychloroquine paired-claims envelope: `event_id: null`, `analyzed_at: null`, DOI / version echo, `materiality_score: 0.9` (significant/major boundary, consistent with a 45%→12% effect-size collapse), `retrieved_patterns_used: ["pattern-demo-001"]` (the COVID effect-size-reduction pattern seeded in Phase 3 — proves MCP `search_drift_patterns` round-tripped correctly), `claim_diffs[0]` correct `numerical_shift` with all four `numerical_delta` numeric fields as JSON numbers (the §3.1.2 JSON-string-vs-number guard added preemptively in this agent's INSTRUCTION per the 5c "generalize" line worked first-shot — no string-number drift). SIGNED delta direction held: `absolute_delta: -33.0`, `relative_delta: -0.7333...`. Two findings worth recording:
  - **MCP transparency confirmed a second time.** Phase 4b-2 had proved `McpToolset(tool_filter=[...])` works for `memory_synthesizer`; this is the same pattern reused verbatim for `drift_analyzer` (single-tool filter `["search_drift_patterns"]` instead of the three-tool filter `memory_synthesizer` uses). Identical INSTRUCTION + same tool schema + same model = identical business behavior. No surprises; the deploy machinery is now mechanical for the remaining MCP-using agents.
  - **§3.2.2 `claim_diffs[]` is N:1 with claim_id pairs, not 1:1.** The deploy returned a SECOND `claim_diff` (`diff_type: "hedging_added"`) on the same `(preprint_claim_id, published_claim_id)` pair as the first `numerical_shift` diff — the LLM correctly identified that "was associated with" + "uncertainty across subgroups" added a hedging dimension distinct from the numerical change. §3.2.2 schema doesn't forbid this and the enum lists both types, so this is correct behavior, not a bug. **Implication for D (frontend) and for the orchestrator**: when rendering or persisting `claim_diffs[]`, do NOT assume `claim_id`-pair uniqueness; key the array elements by index, not by claim_id-pair. TS types should reflect this (one diff per row, not one row per claim pair).
- 2026-05-24 [Jiayu Zhu] **Phase 5d complete — `openalex_citing_works` Workflow YAML tool live on Elastic MCP server.** Decision against (A) ES|QL and (C) inline Python FunctionTool, in favor of (B) Workflow YAML for architectural consistency: all 5/5 agents now route tool calls through the Elastic MCP server, not 4/5. (A) was ruled out because Jeremy's `ingestion/pullers/openalex_client.py` is on-demand-only (OpenAlex's 200M-work corpus is unsuitable for preloading), so no ES index exists to query. (C) would have been faster but introduced an architectural exception ("citation_finder is the only agent that doesn't go through MCP") — fixable in code but visible in the demo tool trace and harder to defend at judging. Implementation captured three substantive findings now codified in the workflow YAML and `agents/_DEPLOY_CHECKLIST.md`-equivalent for tool authoring:
  - **Workflow YAML registration API exists.** Phase 4a-3/4a-4 had registered `create_drift_pattern` / `update_drift_pattern` via UI paste because no script existed. Research surfaced `POST /api/workflows?overwrite=true` (Kibana 9.4+), body shape `{workflows:[{id, yaml}]}`. New `elastic/agent_builder/scripts/upsert_workflow.sh` (idempotent; single endpoint handles create+update) covers this gap. Also retroactively makes Phase 4a workflows reproducible via script.
  - **Workflow YAML input `type` enum is `string|number|boolean|choice|array` — NOT `integer`.** First Spike failed with `Value must be "string" | "number" | "boolean" | "choice" | "array". Ln 38, Col 15` on `type: integer`. Fixed to `type: number`. The UI editor surfaces this validation inline (red underline + error footer), which is faster than the API's `valid:false` response.
  - **`http` step output envelope shape (Spike 2 finding).** `steps.<step_name>.output` for an `http` step is `{ status:<int>, statusText:<str>, headers:{...}, data:<parsed JSON body> }`. The body field is named `data` (not `body`), and JSON bodies are AUTO-PARSED — `json_parse` filter is not needed and applying it to an already-parsed object errors. The correct chained-step reference for our case is `steps.fetch_source.output.data.id | split: '/' | last` (not `output.body | json_parse | map: ...`). This is critical to record because Elastic Workflows' docs do not document the `http` step output schema explicitly; the only way to find it was an output-shape probe (single-step run, inspect the tool execution response). Future Workflow YAML authoring against external HTTP APIs should follow the same probe-first pattern.
  - **LiquidJS `cgi_escape` filter is non-functional in this Workflows build.** Despite being a standard LiquidJS filter (and despite Elastic docs stating non-listed filters are "standard LiquidJS"), `cgi_escape` returns an empty string or causes the URL to be malformed. Spike 1 failed with 404 because `{{ inputs.source_doi | prepend: 'https://doi.org/' | cgi_escape }}` produced an empty path segment. Spike 2+ work without it because OpenAlex's server-side path normalization tolerates the raw `https://doi.org/` segment (un-encoded `/` and `:`). For v0 demo DOIs this is fine; if a future DOI contains `?`, `#`, `&`, or `%` it will need chained `replace` filters as a fallback. The `url_encode` filter (which IS confirmed working from docs) is also insufficient for full URL-segment encoding per Shopify/LiquidJS semantics (doesn't escape `/` and `:`).
  - **3-stage Spike methodology worth keeping.** Each spike isolated one risk dimension: Spike 1 = workflow registers + tool wrapper auto-derives params + http step type is supported + auth/headers work (validated by getting a 404 from OpenAlex, which proves the request actually went out); Spike 2 = `http` step output shape probe (single-step run, inspect tool execution response); Spike 3 = chained two-step with correct output reference syntax + real DOI returning real results. End state verified with Watson-Crick 1953 DNA paper (DOI `10.1038/171737a0`): `data.meta.count` in thousands, `data.results[]` populated with real citing-paper DOIs, titles, authorships. Methodology applies to all future external-HTTP Workflow tools.
- 2026-05-24 [Jiayu Zhu] **Phase 5e complete — `citation_finder` MCP-ified and deployed to Vertex AI Agent Engine (resource `6997171602643222528`); 5/5 agents are now on Agent Engine.** The v0 fabrication scaffold (sentinel DOIs, `SYNTHETIC_V0_PLACEHOLDER` markers) is retired; `agents/citation_finder/agent.py` now calls the Phase 5d `openalex_citing_works` MCP tool and maps the real OpenAlex response to §3.3.2 `affected_citations`. Smoke test used real preprint DOI `10.1101/2023.07.26.23293038` (medRxiv, NAFLD QSOX1/IL1RAP biomarkers — pulled from the `preprints` index where `record_source != "demo_seed"`, i.e. Jeremy puller-ingested), pre-flighted via `openalex_citing_works._execute` to confirm `meta.count=4` (the other two real preprint DOIs in the index had `meta.count=0` — too new to have accumulated citations, useful negative case to keep in mind for future smoke tests). Three findings worth recording:
  - **Path A severity decision** (resolves the §3.3 `severity_tier` design tension): the agent judges severity from the citing paper's TITLE plus `drift_summary`, NOT from sentence-level citation context. Per §1.3 the project deliberately does NOT fetch citing-paper PDFs, and OpenAlex does not return `referenced_works_contexts` in our request (we did not add it to the `select=` clause in 5d either — industry survey showed citation-context retrieval is fundamentally OA-limited even for paid players like scite.ai which licenses ~40 publishers; nobody solves it at scale without publisher deals or PDF acquisition we won't do). INSTRUCTION mandates `severity_reasoning` to explicitly state "Inferred from title; no citation context available — per project §1.3 we do not fetch citing-paper PDFs." Smoke test verified this disclaimer appears in all 4 returned citations. Alternative considered (path b: add `referenced_works_contexts` to 5d's `select=` and let the agent use context when OpenAlex happens to provide it) was rejected for Phase 5 scope hygiene — it's a one-line workflow-YAML change that can be done later if a demo case shows context is available. **Two other Path A decisions for record**: (1) `citing_paper_authors[].email` is ALWAYS null at this agent's boundary; a separate enrichment step (outside the 5 agents' scope) will fill emails for Notifier. (2) `total_found` comes from `meta.count` (= total citations in OpenAlex), `processed` from `len(affected_citations)` (= what the agent mapped) — these can diverge when `per_page` truncates; smoke test happened to have `meta.count == per_page-truncated len == 4` so they're equal, but the contract is clear.
  - **Field-mapping invariants verified end-to-end on the deployed runtime**: (a) `citing_paper_doi` stripped of `https://doi.org/` prefix — §7.2 bare DOI form held for all 4 results; (b) `citing_paper_authors[].orcid` stripped of `https://orcid.org/` prefix — held for all author entries that had ORCIDs; (c) `citation_context: null` for all results; (d) `found_at: null` (orchestrator fills, same rule as §3.2.2 / §3.4.2 / §3.5.2); (e) no fabricated DOIs — every returned `citing_paper_doi` round-tripped against `https://api.openalex.org/works/{doi}`. The hydroxychloroquine-style sentinel DOIs (`10.0000/synthetic-*`) that v0 emitted are GONE.
  - **UTF-8 mojibake in Vertex Playground UI is a renderer bug, NOT a data bug** (worth documenting because it scared us during smoke test and could scare other agents too). Smoke-test output displayed in Playground showed classic UTF-8→cp1252 mojibake on non-ASCII author names (`Nicolás` → `NicolÃ¡s`, `Kjær` → `KjÃ¦r`, `Grønbæk` → `GrÃ¸nbÃ¦k`) and even on my INSTRUCTION's `§` and em-dash characters. Initially feared this was a packaging / encoding problem in `adk deploy agent_engine`. Diagnosis-by-research-agent and direct-SDK verification proved otherwise: `vertexai.agent_engines.get(...).stream_query(...)` returned the SAME response with `repr()` showing clean codepoints (`'Nicolás'`, `'§1.3'`, `'—'`). The Playground UI doesn't declare UTF-8 charset to the browser, so the browser falls back to cp1252 rendering. **Implication for downstream**: 5f supervisor / Cloud Run dispatcher / frontend BFF all consume Agent Engine via REST/SDK and will see clean UTF-8; only Playground UI is affected, and that's a Google-side UI bug we don't fix. **Implication for future agent deploys**: do NOT cargo-cult `PYTHONIOENCODING=utf-8` / `LANG=C.UTF-8` into `.env.deploy` — those have no effect on Agent Engine (it pickles the agent, doesn't re-parse the source file, and the container's locale is already C.UTF-8). The Phase 4b-5 `.env.deploy` Pitfall #7 list in `_DEPLOY_CHECKLIST.md` stays as-is.
  - Phase 5 sub-agent deployment phase (5a-5e) is now done. Remaining: 5f (supervisor + dispatcher + scheduled Workflow per §9.6.1 inverted topology) and 5g (memory_synthesizer as supervisor's final async step). With all 5 sub-agents on Agent Engine + the 4 MCP tools live, 5f is unblocked.
- 2026-05-24 [Jiayu Zhu] **Phase 5f kickoff — three findings before any code is written; supervisor implementation shape and dispatcher scope corrected from the §9.6.1 sketch.**
  - **ADK has no `RemoteAgent` / `AgentEngineAgent` class to wrap a deployed `reasoningEngines/<id>` as a sub-agent of `SequentialAgent`/`ParallelAgent`.** Verified against installed `google-adk==1.34.0` (grepped `agents/.venv/lib/python3.12/site-packages/google/adk/` for `reasoning_engine|ReasoningEngine|AgentEngine|agent_engines` — zero matches under `google/adk/agents/`). The shipped `RemoteA2aAgent` speaks the A2A protocol over HTTP, not the Vertex `streamQuery` RPC. Implication: supervisor cannot be written as `SequentialAgent([RemoteAgent(claim_extractor_id), ...])` (~50 lines declarative). The canonical alternative — endorsed by the ADK custom-agents doc at https://adk.dev/agents/custom-agents/ — is a custom `BaseAgent` subclass whose `_run_async_impl(ctx)` calls `vertexai.agent_engines.get(id).stream_query(...)` for each sub-agent and yields `Event` objects. Estimated supervisor size: ~150 lines (custom BaseAgent + manual session-state shuttling between sub-agents + dynamic per-citation notifier fan-out via the `_merge_agent_run` helper at `parallel_agent.py:51-86`).
  - **`ParallelAgent.sub_agents` is fixed at construction time** — no hook to inject a list derived from previous step output. So §4.1's "fan-out notifier per affected citation" (N unknown at supervisor compile time) cannot use stock `ParallelAgent`; it requires the same custom `BaseAgent` pattern reading `ctx.session.state["affected_citations"]` and constructing N concurrent `notifier.stream_query` calls dynamically. Confirmed against `parallel_agent.py:177` (single iteration over `self.sub_agents` at invocation start).
  - **Dispatcher size in §9.6.1 ("~30 lines Python") underestimated.** Actual scope after schema audit (§2.2.3 / §2.2.4 / §2.2.6 writes + §3.4 SMTP dispatch, both of which the original §9.6.1 sketch elided): bearer auth + ES reverse lookup of full preprint+published rows (Workflow only passes the two DOIs, not full payloads) + GCP token mint + supervisor `stream_query` call + stream event parsing + ES bulk-write 3 indices + Gmail API send + `notification_log` status updates. Estimated ~200-250 lines Python. Personal-Gmail OAuth (refresh-token in Secret Manager, no domain-wide delegation possible on a non-Workspace account — confirmed via Google Workspace docs https://support.google.com/a/answer/162106) adds a one-time `scripts/gmail_oauth_setup.py` outside the dispatcher itself. Email send mechanism chosen: Gmail API on the team's personal Gmail (`gregjones11235@gmail.com`); free, 500/day mailbox cap is plenty for a single-recording demo. Rejected SendGrid for demo authenticity reasons (visible `From:` header on the team's real Gmail vs. transactional provider).
  - **Work split**: C takes Track 1 = supervisor_agent ADK custom BaseAgent + deploy + scheduled Elastic Workflow YAML + OAuth setup script + changelog. A (newly free from prompt-iteration scope) takes Track 2 = entire Cloud Run dispatcher service. Onboarding doc for A at `apps/dispatcher/ONBOARDING.md`. The two tracks are decoupled via a stub stream JSON (A develops against a hand-crafted stub of the supervisor event stream; C delivers a real captured stream once supervisor is deployed); only the final E2E smoke test requires both halves ready simultaneously.
  - **Out of scope for 5f**: §6.1 SSE adapter (the BFF-side translator from ADK native events to the frontend `{event_type, agent_id, drift_event_id, timestamp, payload}` envelope) — explicitly deferred. The frontend (D, Ranjan) can either consume ADK native events directly via the Cloud Run dispatcher's eventual webhook, or wait for the adapter; decision deferred to post-5f. Pullers → Cloud Run Job + Cloud Scheduler (B, Jeremy) is a parallel independent track; B's `411e4a5` commit ("Add OpenAlex affected citation candidate flow") is being reverted because the v0-era OpenAlex-candidate double-write path it introduced (ingestion-time `severity_tier="pending"` writes to `affected_citations`) was superseded by Phase 5e's `citation_finder` MCP-tool integration that pulls real OpenAlex citing works synchronously at agent-run time. Revert removes 9 of 10 files in that commit; the `notifier/agent.py` "do not draft for pending" guard is kept as cheap defense-in-depth.
- 2026-05-24 [Jiayu Zhu] **Phase 5f-i + 5g complete — supervisor_agent deployed to Vertex AI Agent Engine (resource `7816826734824652800`); §4.1 main flow + 5g memory-loop side flow run end-to-end on the deployed runtime against the canonical HCQ demo envelope.** Three substantive findings now codified into `agents/_DEPLOY_CHECKLIST.md` (Pitfall #8) and the in-repo supervisor source:
  - **Cross-reasoning-engine calls need an explicit IAM grant on the Agent Engine service agent.** First smoke test failed at `claim_extractor` fan-out with `PERMISSION_DENIED: aiplatform.reasoningEngines.get denied on resource //aiplatform.googleapis.com/projects/751133713115/locations/us-central1/reasoningEngines/2286406392413683712`. Root cause: a deployed Agent Engine runs as the Google-managed AI Platform Reasoning Engine Service Agent (`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`, auto-provisioned on first Agent Engine deploy in a project). Its default `roles/aiplatform.reasoningEngineServiceAgent` covers its own runtime needs (logging, telemetry, model invocation) but NOT permission to call OTHER reasoning engines. Fix: project-level grant of `roles/aiplatform.user` to that SA via `gcloud projects add-iam-policy-binding tensile-topic-496519-i1 --member="serviceAccount:service-751133713115@gcp-sa-aiplatform-re.iam.gserviceaccount.com" --role="roles/aiplatform.user"`. Binding is project-level and takes effect at runtime immediately — no redeploy needed. There is no narrower predefined role for `reasoningEngines.get` + `.streamQuery`; custom per-engine SAs are configurable via Python SDK (`client.agent_engines.create(config={"service_account": ...})`) but NOT via `adk deploy agent_engine` CLI as of `google-adk` 1.34.0 (tracked in https://github.com/google/adk-python/issues/2951). The previous 5 sub-agents never hit this because their only outbound calls were to Elastic MCP (which carries its own API key), not other reasoning engines.
  - **`_extract_final_output` must handle markdown code fences.** Second smoke test failure: supervisor `_extract_final_output` returned `None` on both `claim_extractor` outputs even though the agents had returned valid §3.1.2 JSON. Two compounding issues: (1) `claim_extractor` is an `LlmAgent` with no tools, so its final output lives in `Event.content.parts[*].text` not in `function_response.response` — the function_response-first scan that works for `drift_analyzer` / `memory_synthesizer` (both McpToolset-bearing) finds nothing for tool-less agents. (2) Gemini frequently wraps "JSON only" output in a triple-backtick markdown code fence (`` ```json ... ``` ``) regardless of INSTRUCTION wording; bare `json.loads(part.text)` raises `JSONDecodeError`. Fix codified at `agents/supervisor_agent/agent.py:_strip_markdown_fence` + `_extract_final_output`: (a) prefer function_response when present; (b) fall back to combining all text parts in the final event (Gemini also occasionally splits one JSON response across multiple text parts in the closing event) and stripping a leading ```...\n / trailing ``` fence before `json.loads`. Generalize: any code that consumes ADK LlmAgent text output as structured data needs the fence-stripping defensive layer; downstream parsers in the dispatcher (A's Track 2) should reuse the same `_strip_markdown_fence` helper.
  - **Sub-agents take JSON-stringified envelopes as their `message`, not structured input.** Vertex AI Agent Engine's wrapper `AdkApp.async_stream_query(message: str | Content, user_id: str, ...)` has no kwargs path for arbitrary structured input — `message` must be either a string or an ADK `Content` dict. Supervisor's `_call_sub_agent` does `engine.async_stream_query(message=json.dumps(envelope, ensure_ascii=False), user_id=...)`; each sub-agent's INSTRUCTION (unchanged since Phase 5a-5e) implicitly handles "input is a JSON-encoded envelope you parse mentally" — Gemini does this without prompt-level coaching. The dispatcher will use the same shape: `supervisor.async_stream_query(message=json.dumps({preprint, published}), user_id="dispatcher::<doi>")`. ONBOARDING.md (`apps/dispatcher/ONBOARDING.md`) was patched to reflect this (the original draft had an `input=envelope` placeholder kwarg that doesn't exist in the AdkApp template). Sync the same call shape into anything else that talks to an Agent Engine ADK runtime.
  - **End-to-end verification on the deployed supervisor.** Canonical HCQ envelope from `elastic/demo_seed/preprints.json` (preprint `10.1101/2024.01.15.123456` v3 + published `10.1016/j.cell.2024.05.001`) submitted via Vertex Playground. Trace (14 visible Playground events): `claim_extractor` ×2 parallel — both returned §3.1.2 (preprint: 2 claims incl. 45% reduction + cohort claim; published: 2 claims incl. 12% reduction + hedged); `drift_analyzer` called `search_drift_patterns` MCP tool (retrieved `pattern-demo-001`) then returned §3.2.2 with `numerical_shift` + `hedging_added` claim_diffs, `materiality_score: 0.85`, `numerical_delta` all numeric (45.0/12.0/-33.0/-0.733 — the 5c JSON-number guard transitively held through), `retrieved_patterns_used: ["pattern-demo-001"]`; `citation_finder` called `openalex_citing_works` MCP tool, returned `affected_citations: []`, `total_found: 0` (expected — demo DOI not in OpenAlex corpus; this is the synthetic-DOI negative case 5e flagged); notifier fan-out correctly SKIPPED (N=0 short-circuits the dynamic ParallelAgent merge); `memory_synthesizer` ran as 5g final step — called `search_drift_patterns` + `update_drift_pattern`, returned §3.5.2 with `action: "update_existing"`, pattern `_version: 6` (was 5 after 4b-6's earlier validation), `synthesized_at: null`. **All §3.x.2 schemas held, all `*_at` wall-clock fields correctly null, no fabricated DOIs, no `record_source` writes from agent-side. Memory loop closure verified end-to-end through the supervisor (5g is no longer optional — it's wired in).** Remaining 5f work: scheduled Elastic Workflow YAML (blocks on A's dispatcher URL) + `_DEPLOY_CHECKLIST.md` status row for supervisor.
  - **Captured stream for A's stub**: `agents/supervisor_agent/scripts/capture_stream.py` replays the above envelope against the deployed supervisor and writes the full ADK event sequence to `apps/dispatcher/_stub_stream.json` (gitignored, ~kilobytes). A's Track 2 dispatcher uses this as the `USE_STUB_STREAM=1` fixture per `apps/dispatcher/ONBOARDING.md` § Stub stream.
