# ClaimDrift

> When a preprint becomes a peer-reviewed paper, its claims can change. ClaimDrift finds what changed between the first
> preprint version and the published paper, finds the citing papers that still rely on the superseded value, and lets
> authors check their own manuscripts against the revised claims.

Built for the [Google Cloud Rapid Agent Hackathon](https://rapid-agent.devpost.com/) (Elastic track). Project page:
<https://devpost.com/software/claimdrift>.

---

## What it does

| Layer | Question | How |
|---|---|---|
| ① Drift analysis | What changed between preprint v1 and the published paper, why, and how much does it matter? | Both full texts from JATS → `claim_extractor` + `drift_analyzer` → every evidence quote verified verbatim against the source (provenance) → two severities kept apart (abstract level, full-text level) → review triggers |
| ② Citation analysis | Which citing papers still use the superseded value? | Program pre-screen of Europe PMC citing papers → orchestrator-workers ReAct agent reads their full texts → every "superseded" verdict re-checked → coverage reported |
| Review & notify | Should a person look first? Who gets told? | Review queue for unverified quotes, severity mismatches, high-severity notices, unclear citations; drafted notices (all mail goes to the project's test inbox) |
| ③ Author self-check | Does my manuscript cite a claim that was later revised? | Reference list (DOI / title) or citing sentences: ELSER + BM25 hybrid retrieval proposes candidates, one Gemini call per sentence judges them |

## Architecture

```
  dispatcher / Playground / CLI
                │ preprint v1 + published (JATS)
                ▼
┌─ Vertex AI Agent Engine ── [1] drift analysis ─────────────────────────────────┐
│  supervisor (fixed DAG)                                                        │
│    1. claim_extractor (flash) x2                                               │
│    2. drift_analyzer (pro)                                                     │
│    3. abstract severity (few-shot)                                             │
│    4. verify_quote (MCP)                                                       │
│    5. review triggers                                                          │
└────────────┬───────────────────────────────────────────────────────────────────┘
             │ drift_events
             ▼
┌─ Vertex AI Agent Engine ── [2] citation analysis: citation_finder (ReAct) ─────┐
│  prefetch (program): candidates + flags F1 / F2 / F3                           │
│                                                                                │
│  orchestrator (pro, <= 14 turns)                                               │
│    overview                                                                    │
│    dispatch      (<= 8 groups x 5 papers) ──┐                                  │
│    search_more   (other spellings)          │                                  │
│    follow_chain  (relaying paper)           │                                  │
│    finish                                   ▼                                  │
│  workers (pro, parallel, fresh context)                                        │
│    get_citation_sentences / search_in_work (MCP)                               │
│    -> superseded | current | flagged_as_previous | indirect | not_relying      │
│       | unclear                                                                │
│  verification (pro): re-check every "superseded"                               │
│  overflow (pro): batch-judge the rest, record coverage                         │
└────────────┬───────────────────────────────────────────────────────────────────┘
             │ affected_citations
             ▼
   review queue (BFF + frontend) ──► notifier (flash) ──► Gmail

   [3] author self-check (BFF):
       sentence ──► ELSER + BM25 top 10 (+ exact lookups) ──► flash judge

┌─ Cloud Run ────────────────────────────────────────────────────────────────────┐
│  claimdrift-mcp         full text, verify_quote, Europe PMC citing papers      │
│  claimdrift-bff         review API, author self-check                          │
│  claimdrift-playground  live 5-agent run (SSE)                                 │
│  claimdrift-frontend    Next.js dashboard                                      │
│  citation-dispatch job  runs queued citation analyses                          │
└────────────────────────────────────────────────────────────────────────────────┘
┌─ Elasticsearch Serverless ─────────────────────────────────────────────────────┐
│  drift_events, affected_citations, citation_runs, notification_log,            │
│  drift_claims_search (ELSER + BM25), preprints, ...                            │
└────────────────────────────────────────────────────────────────────────────────┘
```

- **supervisor** runs a fixed sequence: `claim_extractor` (flash) reads both versions, `drift_analyzer` (pro) compares
  them in one call, every evidence quote is then checked verbatim in the source through the MCP tool service, and review
  triggers are set.
- **citation_finder** is the one agent loop. A program pre-screens the citing papers that contain the old value; the
  orchestrator then decides, from what the workers report, which candidates to dispatch next, whether to search again
  with other spellings of the old value, whether to follow a chain through a paper that relayed the value, and when to
  stop. Each worker reads its papers' full texts in a fresh context. Every "still uses the old value" verdict is checked
  once more, and the share of candidates actually judged is recorded.
- **MCP tool service** (Cloud Run, private): the agents' only access to paper text and citing literature (full-text
  sections and tables, verbatim quote verification, Europe PMC).
- **Author self-check** (BFF): ELSER + BM25 hybrid retrieval proposes the top 10 candidate changes for a sentence; one
  flash call decides which ones the sentence cites and whether it uses the old or the current value.

## Results (held-out or gold-labelled; details in the linked write-ups)

| What | Result | Source |
|---|---|---|
| Citation analysis, orchestrator-workers vs. non-agent batch judgement on the same candidates (2 targets, 48 relying papers) | 28 vs. 14 relying papers found; precision 90% | `(可选)初筛引入ELSER实验.md` §2.1 |
| Author self-check, end to end, held-out test split (489 sentences, self-check gold) | sentences citing a revised claim found 92%, old-value sentences 19/19, old/new value verdict 97% | `作者自查ELSER预筛实验.md` §4.4 |
| Self-check candidate retrieval, library padded with ~3,000 cross-field abstracts | Recall@10: hybrid 92%, ELSER 91%, BM25 85% | `作者自查ELSER预筛实验.md` §2.2 |
| Drift analysis needs full text | root cause right: abstract only 27/37, full text 35/37 | `新系统改造设计.md` §8 |

Limitations are stated in each write-up (small number of targets, single-annotator gold sets, open-full-text scope).

## Repository layout

| Path | Contents |
|---|---|
| `claimdrift/` | The Python package: pipeline (`pipeline.py`), drift analysis, citation analysis (`citations/`), review, notifier, author self-check (`selfcheck.py`), MCP server and client, prompts, CLI (`python -m claimdrift`), tests, scripts |
| `agents/` | The five ADK agents deployed to Vertex AI Agent Engine (thin wrappers around `claimdrift.agent_handlers`) and `engines.json` (their resource names) |
| `apps/` | Cloud Run services: `bff/`, `playground/`, `mcp/` (image of the tool service), `dispatcher/` (automatic pipeline) |
| `frontend/` | Next.js dashboard: review queue, author self-check, playground |
| `ingestion/` | bioRxiv / medRxiv / Crossref pullers feeding the `preprints` index |
| `elastic/` | Index mappings and index-creation scripts; `agent_builder/` holds the Elastic Workflow that triggers the automatic pipeline |
| `deploy/` | `cloudrun.sh` (MCP, BFF, Playground, job, frontend), `pipeline.sh` (automatic pipeline) |
| `docs/` | `contracts.md` (interface specification and changelog; §10 holds the frontend ↔ BFF types) and ops notes |
| `data/` | Not in git: case bank, docstore, Europe PMC cache, experiments and gold sets. On Cloud Run the same tree is the Cloud Storage bucket mounted at `/data` |
| `新系统改造设计.md`, `新系统云端部署.md` | System design and the cloud deployment record |
| `作者自查ELSER预筛实验.md`, `(可选)初筛引入ELSER实验.md`, `agent项目升级改造.md`, `作者自查prompts.md` | Experiment write-ups and the self-check test cases |

## Setup (WSL / Linux)

Requirements: [uv](https://docs.astral.sh/uv/), Python 3.12, Node (via nvm) for the frontend, the gcloud CLI logged in
to the project (ID tokens for the private MCP service; deployment).

```bash
uv sync                      # one environment for the whole repo (pyproject.toml / uv.lock at the root)
cp .env.example .env         # fill in GEMINI_API_KEY, ELASTIC_ENDPOINT, ELASTIC_API_KEY, ...
uv run python -m unittest claimdrift.tests.test_offline
```

By default every command uses the **cloud** system (Elasticsearch Serverless, the MCP service on Cloud Run, the agents
on Agent Engine). `CLAIMDRIFT_LOCAL=1` switches to the local stack: docker Elasticsearch `claimdrift-es` on `:9200`
(local ELSER), a stdio MCP server per client, every agent in-process.

### Run locally

```bash
bash dev.sh            # BFF :8787 + Playground :8799 + frontend :3000 (review: /review, self-check: /selfcheck)
```

### CLI

```bash
bash claimdrift/run.sh analyze incubation_travellers_eurosurv     # layer ①; 'all' = the case bank
bash claimdrift/run.sh citation-worker --once                     # layer ② (async; minutes per target)
bash claimdrift/run.sh review-queue
bash claimdrift/run.sh notify --dry-run
bash claimdrift/run.sh selfcheck-sentence "We assumed a mean incubation period of 5.8 days [12]."
bash claimdrift/run.sh selfcheck-index                            # rebuild drift_claims_search from drift_events
bash claimdrift/run.sh test                                       # offline + MCP tests
```

## Deployment

```bash
uv run python claimdrift/scripts/deploy_agents.py ...   # the five Agent Engine agents (updated in place, names kept)
bash deploy/cloudrun.sh mcp|build|bff|playground|job|frontend|all
```

Rebuild the BFF / Playground images after redeploying the agents (the images copy `agents/engines.json`). In a
non-interactive WSL shell put `~/google-cloud-sdk/bin` on `PATH` first. Step-by-step record, IAM and smoke tests:
`新系统云端部署.md`.

## License

See [LICENSE](LICENSE).
