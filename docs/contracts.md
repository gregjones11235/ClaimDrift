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

**TODO B (Day 3-4)**: Full mapping JSON, including ELSER inference endpoint, shards/replicas, refresh interval.

```json
// TODO B: fill in complete mapping JSON
{
  "mappings": { ... },
  "settings": { ... }
}
```

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

**TODO B (Day 3-4)**: Full mapping JSON.

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
- `retrieved_patterns`: nested | null (patterns retrieved from drift_patterns and actually used here, structure in §3.2.2)

**TODO B (Day 3-4)**: Full mapping JSON.

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

**TODO B (Day 3-4)**: Full mapping JSON.

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

**TODO B (Day 3-4)**: Full mapping JSON. **Especially**: `pattern_description` must be wired through ELSER semantic retrieval — Drift Analyzer's retrieval depends on it.

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

**TODO B (Day 3-4)**: Full mapping JSON.

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

- Memory Synthesizer calls `search_drift_patterns` itself (no longer pre-injected by the orchestrator) using `drift_event.drift_summary` as the query, `top_k=5`.
- **No score threshold.** The RRF score returned by `retriever.rrf` over our small index is rank-based (typically 0.01–0.05) and does not measure semantic similarity in a way that supports a fixed cutoff — see probe results in `agents/scripts/probe_rrf_scores.py`. The original §3.5.1 v0 design used `similarity_score >= 0.75` as a hard rule; that rule has been retired.
- The agent is instead instructed to **read** each retrieved pattern's description and judge "is this the same underlying phenomenon as the new drift_event?" per-candidate. Decision is made by the LLM, not by the score.
- If yes → call `update_drift_pattern(pattern_id, source_event_id)`. If no candidate qualifies → call `create_drift_pattern(...)`. The two-tool shape (rather than a single upsert) keeps the create-vs-update decision visible in the tool the LLM chooses.

**v0 update scope** (intentional minimum):

- `update_drift_pattern` only appends `source_event_id` to `source_event_ids` and refreshes `last_updated_at`. `pattern_description` / `domain_tags` are not refined.
- **TODO (post-v0)**: extend `update_drift_pattern` with optional `pattern_description_refinement` and `domain_tags_to_add` parameters, so a long-lived pattern can broaden its description as new domains accumulate (e.g. "COVID-related" → "COVID-related and other respiratory virus" once cross-domain events show up). Defer until prompt iteration shows v0's narrow-description failure mode is hurting retrieval quality.

**drift_event ← pattern back-link** (out of scope for this agent):

- Memory Synthesizer does NOT write `pattern_id` back into `drift_event.retrieved_patterns`. That double-link is the orchestrator's job (Elastic Workflows step running after the agent), so the agent stays single-purpose.

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

### 5.3 ELSER semantic hookup

The `semantic_text` fields in `preprints`, `claims`, and `drift_patterns` must be wired through ELSER semantic retrieval.

**Serverless implementation note (B)**: Use `semantic_text` with an explicit `.elser-2-elastic` `inference_id`. Do not attach an ingest inference pipeline that writes ELSER output back into the same `semantic_text` field, because `semantic_text` expects the indexed document field to remain a scalar text value.

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

## 9. Production deployment architecture (Phase 4)

§3–§8 describe the **business semantics** of the system. §9 describes the **production deployment shape** required by the Google Cloud Rapid Agent Hackathon — Elastic Track. v0 (Phases 1-3) ran locally via `adk web` against a serverless Elasticsearch project; v1 (Phase 4) moves the same business logic onto managed runtimes without changing it.

### 9.1 Rules-driven constraints

The hackathon rules ([rapid-agent.devpost.com/rules](https://rapid-agent.devpost.com/rules)) impose three hard constraints that determine the deployment shape:

1. **Gemini-only for project-runtime AI.** All other AI tools are not permitted in the running project. (Auxiliary dev tooling like AI-assisted code editors is not in scope.)
2. **Project must use Google Cloud Agent Builder.** The Agent Builder platform now bundles a code-first SDK (ADK), a managed runtime (Vertex AI Agent Engine), and Agent Studio. **ADK code deployed to Agent Engine satisfies this requirement** — confirmed via the official "Agent Builder → ADK overview" docs ([cloud.google.com/products/agent-builder](https://cloud.google.com/products/agent-builder)). We do NOT have to rewrite the Phases 1-3 ADK agents.
3. **Elastic track requires integrating the Partner Entity's MCP server.** Elastic Agent Builder ships a built-in MCP server that natively exposes whatever custom tools we define inside it. We MUST route tool calls through this server (not a self-written MCP shim) to satisfy the rule literally.

### 9.2 Target architecture

```
Vertex AI Agent Engine (managed)
   - drift_analyzer (ADK LlmAgent, gemini-2.5-pro)
   - memory_synthesizer (ADK LlmAgent, gemini-2.5-pro)
        │
        │ MCP protocol (tool calls)
        ▼
Elastic Agent Builder built-in MCP server
   - search_drift_patterns       (ES|QL tool)
   - create_drift_pattern        (Elastic Workflow YAML)
   - update_drift_pattern        (Elastic Workflow YAML)
        │
        │ Elasticsearch APIs
        ▼
Elasticsearch Serverless (drift_patterns / drift_events / claims / ...)
```

Other agents in §3 (claim_extractor, citation_finder, notifier) follow the same shape but use simpler tools — they are not part of the memory-loop critical path that Phase 4 prioritizes.

### 9.3 Tool migration plan (function tool → Elastic Agent Builder tool)

Phases 1-3 implemented the three tools as Python functions in `agents/_shared/elastic_retrieval.py` and `agents/_shared/elastic_write.py`. The behavioral contract (top-k retrieval surfaces candidates; LLM does its own rerank by reading `pattern_description`; UUID v4 mint on create; GET-then-merge-dedup-then-PUT on update; 404-raises-loudly) is spec-frozen — Phase 4a re-expresses them inside Elastic Agent Builder while preserving every observable behavior.

| Python tool | Target shape in Elastic Agent Builder | Notes |
|---|---|---|
| `search_drift_patterns` | **ES\|QL tool** — single-`MATCH` query against `pattern_description` (semantic_text, ELSER-routed). Parameters: `query_text`, `top_k`, `exclude_demo_seed`. | No `retriever.rrf` wrapper — see 2026-05-23 changelog finding. The Phase 1 RRF design was the same source fused with itself on this index, so single-`MATCH` is strictly simpler and not less expressive. The Python implementation in `agents/_shared/elastic_retrieval.py` stays in the repo as reference spec (not runtime). |
| `create_drift_pattern` | **Elastic Workflow** (YAML) — single `elasticsearch.request` step: `PUT _doc/{id}?op_type=create&refresh=wait_for`. UUID v4 and ISO 8601 `now_iso` are pushed up to the caller (the LLM) because Workflows YAML has no native `uuid()` / `now()` step in the current preview. `record_source` left unset per §2.3. | `op_type=create` makes (vanishingly rare) UUID collisions surface as an ES error instead of silent overwrite. Array inputs (`domain_tags`) MUST use Liquid type-preserving syntax `"${{ inputs.domain_tags }}"` — bare `{{ }}` stringifies arrays/objects/numbers/booleans (caught and fixed during Phase 4a-5; see 2026-05-23 changelog). |
| `update_drift_pattern` | **Elastic Workflow** (YAML) — single `elasticsearch.request` step: `POST _update/{id}?refresh=wait_for` with a painless script that performs the read-modify-write atomically on the ES side: set-union dedup of `source_event_ids`, recompute `support_count = source_event_ids.size()`, refresh `last_updated_at`, preserve `created_at` / `pattern_description` / `pattern_type` / `domain_tags`. 404 on the underlying `_update` surfaces as a workflow step failure (`document_missing_exception`), not a silent fallback to create. | Painless was chosen over a two-step GET→PUT to (a) keep atomicity / version-token protection, and (b) avoid relying on Workflows YAML expression language to do array set-union between steps (Liquid doesn't expose set ops). v0 append-evidence-only scope held: `pattern_description` / `domain_tags` are intentionally NOT refined here — see §3.5.1 post-v0 TODO. |

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
- 2026-05-23 [Jiayu Zhu] **Phase 4 complete.** All three Phase-3 Python tools migrated to Elastic Agent Builder (one ES|QL tool + two Workflow YAML tools; §9.3); `memory_synthesizer` ADK agent deployed to Vertex AI Agent Engine (resource `8580327609152307200`); MCP transport (`POST $KIBANA_URL/api/agent_builder/mcp`) connects the two with `tool_filter` restricting the LLM to our three drift tools. Every §3.5.1 / §3.5.2 invariant verified end-to-end on the deployed runtime. Hackathon rule compliance (§9.1): (a) Gemini-only at runtime ✓ (`gemini-2.5-pro`); (b) Google Cloud Agent Builder ✓ (Agent Engine is the Agent Builder platform's managed ADK runtime); (c) Partner Entity's MCP server ✓ (Elastic's own MCP server, not a self-written shim). The Phase-3 Python implementations at `agents/_shared/elastic_retrieval.py` and `agents/_shared/elastic_write.py` remain in the repo as regression harness per §9.5. Phase 4c (Devpost packaging + demo video + frontend wiring by D) is now unblocked.
