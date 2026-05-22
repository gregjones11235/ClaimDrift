# ClaimDrift — Inter-component Contracts

> **Status**: v0 (Day 1-2)
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

B fills in the mapping for each index. Below are the minimum field constraints — B adds analyzers, index options, and the ELSER pipeline hookup on top.

#### 2.2.1 `preprints` index

**Minimum fields** (B must include):

- `doi`: keyword
- `source`: keyword (`arxiv` | `biorxiv` | `medrxiv`)
- `version`: keyword (e.g. `v1`, `v2`)
- `is_final_preprint`: boolean
- `published_doi`: keyword | null (the final published DOI from Crossref; null means not yet published)
- `title`: text + keyword subfield
- `abstract`: semantic_text (routed through ELSER pipeline)
- `conclusion`: semantic_text | null
- `authors`: nested, containing `name` (keyword), `orcid` (keyword | null), `affiliation` (text)
- `posted_date`: date (ISO 8601)
- `ingested_at`: date

**TODO B (Day 3-4)**: Full mapping JSON, including ELSER pipeline name, shards/replicas, refresh interval.

```json
// TODO B: fill in complete mapping JSON
{
  "mappings": { ... },
  "settings": { ... }
}
```

#### 2.2.2 `claims` index

**Minimum fields**:

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

**TODO B (Day 3-4)**: Full mapping JSON.

#### 2.2.3 `drift_events` index

**Minimum fields**:

- `event_id`: keyword (UUID)
- `preprint_doi`: keyword
- `preprint_version_compared`: keyword (which version was compared against published)
- `published_doi`: keyword
- `detected_at`: date
- `drift_summary`: text (human-readable summary generated by Gemini)
- `claim_diffs`: nested, structure in §3.2.2
- `materiality_score`: float (0.0-1.0, overall "severity" of this drift)
- `retrieved_patterns`: nested | null (patterns retrieved from drift_patterns and actually used here, structure in §3.2.2)

**TODO B (Day 3-4)**: Full mapping JSON.

#### 2.2.4 `affected_citations` index

**Minimum fields**:

- `affected_citation_id`: keyword (= _id, `{drift_event_id}::{citing_doi}`)
- `drift_event_id`: keyword
- `citing_paper_doi`: keyword
- `citing_paper_title`: text
- `citing_paper_authors`: nested (name, orcid, email | null)
- `citation_context`: text | null (if OpenAlex provides surrounding context; usually null because we don't fetch PDFs)
- `severity_tier`: keyword (`central` | `comparative` | `peripheral`)
- `severity_reasoning`: text (Gemini's justification)
- `scored_at`: date

**TODO B (Day 3-4)**: Full mapping JSON.

#### 2.2.5 `drift_patterns` index ⭐

> This is the core index for the memory loop. Field design must let Drift Analyzer retrieve efficiently.

**Minimum fields**:

- `pattern_id`: keyword (UUID)
- `pattern_description`: semantic_text (human-readable + ELSER searchable, **this is the memory loop's retrieval field**)
- `pattern_type`: keyword (`numerical_softening` | `hedging_addition` | `claim_disappearance` | `effect_size_reduction` | `other`) // TODO A: may expand after Memory Synthesizer prompt iteration
- `domain_tags`: keyword (array, e.g. `["covid-19", "clinical-trial", "rct"]`)
- `source_event_ids`: keyword (array, list of drift_events that produced this pattern)
- `support_count`: integer (number of drift_events supporting this pattern — higher = more reliable)
- `created_at`: date
- `last_updated_at`: date

**TODO B (Day 3-4)**: Full mapping JSON. **Especially**: `pattern_description` must be wired through the ELSER pipeline — Drift Analyzer's retrieval depends on it.

#### 2.2.6 `notification_log` index

**Minimum fields**:

- `affected_citation_id`: keyword (= _id)
- `drift_event_id`: keyword
- `recipient_email`: keyword
- `subject`: text
- `body`: text
- `status`: keyword (`drafted` | `sent` | `bounced` | `failed`)
- `sent_at`: date | null
- `error_message`: text | null

**TODO B (Day 3-4)**: Full mapping JSON.

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
    "extracted_at": "2026-05-20T12:34:56Z"
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
  "published_claims": [ /* claim objects from claims index */ ],
  "retrieved_patterns": [                              // memory loop read side, may be empty array
    {
      "pattern_id": "...",
      "pattern_description": "...",
      "pattern_type": "...",
      "domain_tags": [ "..." ],
      "support_count": 5,
      "similarity_score": 0.82                          // ELSER retrieval score
    }
  ]
}
```

**Retrieval rules** (critical for the memory loop):

- Before being called, Drift Analyzer uses the preprint's abstract to run a hybrid search (ELSER + BM25) against `drift_patterns`
- Take top-3 results, filter for `similarity_score >= 0.7`
- Filtered patterns are injected as `retrieved_patterns`
- If none, pass empty array; Drift Analyzer works as usual

The `0.7` threshold is a v0 guess; tune after Week 2 once we see how it works.

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

**NOTE (v0 finding, 2026-05-21)**: Citation Finder v0 has no `openalex_puller` tool wired yet and FABRICATES plausible-looking DOIs (using real journal prefixes like `10.1038/...`, `10.1016/...`). Until the OpenAlex tool is wired (Step B/C), v0 outputs must NOT be written to the `affected_citations` index, and any v0 result shown to humans must carry a synthetic marker. Prompt must use sentinel format like `10.0000/synthetic-v0-NNN` OR set `citation_context = "SYNTHETIC_V0_PLACEHOLDER"`.

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
  "drafted_at": "2026-05-20T...",
  "dispatch": {                                         // Notifier triggers dispatch synchronously, result recorded here
    "status": "sent",                                   // "sent" | "bounced" | "failed" | "skipped"
    "sent_at": "2026-05-20T...",
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
  },
  "existing_similar_patterns": [                         // may be empty; used to decide create vs update
    {
      "pattern_id": "...",
      "pattern_description": "...",
      "similarity_score": 0.78
    }
  ]
}
```

**existing_similar_patterns retrieval rules**:

- Before firing, Memory Synthesizer uses the drift_event's `drift_summary` to search `drift_patterns`
- Take top-5, filter for `similarity_score >= 0.75`
- If matches exist, Memory Synthesizer **updates** the existing pattern's `support_count` instead of creating new
- If none, **creates** a new pattern

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
  "synthesized_at": "2026-05-20T..."
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

Orchestrated by Elastic Workflows. TODO C (Day 8-10): implement the above topology in workflow YAML / config, and document the workflow file path in the README.

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

**TODO B (Day 3-5)**: Implementation details per puller (batch size, error handling, retry policy, log format).

### 5.3 ELSER Pipeline hookup

The `semantic_text` fields in `preprints` and `claims` must be wired through an ELSER ingest pipeline.

**TODO B (Day 3)**: Provide the ingest pipeline JSON definition and how each mapping references it.

---

## 6. BFF / Server-Sent Events event format

The frontend receives real-time agent state from the BFF via SSE. Events are emitted by agents in Agent Builder; the BFF forwards them.

### 6.1 Event type list (C drafts, D gives feedback)

Events are emitted by agents in Agent Builder; C decides which to emit when configuring each agent. D is the frontend consumer and has the final say on event granularity and payload content.

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
| `agent.tool_call` | agent invokes an ES|QL tool | `{ tool_name, args }` |
| `agent.pattern_retrieved` | **Drift Analyzer retrieved patterns (memory loop key event, frontend should highlight)** | `{ pattern_ids, similarity_scores }` |
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
- 2026-05-22 [Jiayu Zhu] B-side ES infrastructure verified end-to-end:
  - All 6 indices live on cluster; 3 semantic_text fields (preprints.abstract/conclusion, claims.text, drift_patterns.pattern_description) backed by inference_id `.elser-2-elastic` (sparse_embedding)
  - `default_pipeline` explicitly set to `_none` on each index (the §2.2 mapping JSON's ingest-pipeline scaffolding turned out unnecessary on serverless 9.5 — `semantic_text` performs inference natively; pipeline + same-field output caused shape conflicts in early testing)
  - ELSER semantic search confirmed working on `preprints.abstract` (7 docs ingested via medrxiv puller + demo seed); `retriever.rrf` (BM25 + ELSER hybrid) confirmed working — this is the production query shape for §3.2 drift_patterns retrieval
  - `drift_patterns` demo seed (`pattern-demo-001`) retrievable; memory loop read path is live
  - §3.2 retrieval rule "similarity_score >= 0.7" needs revisit — RRF scores are rank-based (0.01–0.05 range), not cosine. Threshold to be re-tuned once MCP retrieval tool runs against real drift_patterns. Tracked as TODO C.
  - **New field `record_source` (keyword)** added by Jeremy on `preprints`, `claims`, `drift_patterns`. Purpose: distinguish demo-seed documents from real puller data. Values seen: `"demo_seed"`; real puller-ingested docs leave the field unset (nullable / optional).