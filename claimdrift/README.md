# claimdrift — the new system (新系统改造设计.md, P0 + P1)

Default = the cloud deployment (新系统云端部署.md, `config.py`): Elasticsearch Serverless (`ELASTIC_ENDPOINT` /
`ELASTIC_API_KEY` in `.env`, ELSER `.elser-2-elastic`), the MCP tool service on Cloud Run (stateless, Google ID
token), and the five agents on Vertex AI Agent Engine (`agents/engines.json`; handlers in `agent_handlers.py`, client
`engines.py`). Gemini through the AI Studio key in every case.

`CLAIMDRIFT_LOCAL=1` = local development stack: docker Elasticsearch `claimdrift-es:9200` (ELSER `elser-local`), a stdio
MCP server per client, every agent in-process. Single settings can still be overridden (`CLAIMDRIFT_ES`,
`CLAIMDRIFT_MCP_URL`, `CLAIMDRIFT_ENGINES`, `CLAIMDRIFT_DATA`). Validation scope is the case bank (`data/cases/`, also in
the bucket mounted at `/data` on Cloud Run).

```
layer ①  pipeline.py        v1 JATS + published JATS -> drift_analyzer -> abstract severity -> 事后校验 (MCP verify_quote)
                            -> review triggers -> drift_events (+ claims, + self-check index) -> queue citation runs
layer ②  citations/jobs.py  citation_runs queue -> orchestra (prefetch, orchestrator, workers via MCP, verification,
                            overflow batch) -> affected_citations (+ coverage) ; rerun_due() = incremental layer ②'
review   review.py          triggers + decisions; a rejected event / citation is never notified (review is not a precondition)
notify   notifier.py        gate re-checked at send time; every message goes to claimdriftnotifier@gmail.com
③        selfcheck.py       author self-check: reference list (DOI / title) and citing sentences (ELSER+BM25, old/new value)
```

| Item | Where |
|---|---|
| 0.1 v1 full text from JATS | `documents.py` (case bank / docstore; on demand via `apps/dispatcher/versions.py` + Europe PMC) |
| 0.2 routing + v4a (default: claims route for every pair, 2026-10-02; `CLAIMDRIFT_ROUTE=auto` restores the length rule) | `drift_analyzer.py`, `prompts/drift_analyzer.py` (byte-identical to the experiment prompt) |
| 0.3 two severities, stored apart | `abstract_severity.py` + `prompts/abstract_exemplars.json` (fixed 12, `split(k=4, seed=7)`); full-text tier per diff |
| 0.4 provenance | `provenance.py` -> `drift_events.provenance` / `verification` |
| 0.5 orchestra as async job | `citations/orchestra.py`, `citations/jobs.py`, index `citation_runs` |
| 0.6 affected_citations | `store.affected_citation_doc`, `elastic/mappings/affected_citations.json` |
| 0.7 notification gate | `review.py`, `notifier.py` |
| 0.8 MCP (stdio locally; stateless streamable HTTP on Cloud Run) | `mcp_server.py`, `mcp_client.py`, `citations/access.py` |
| 0.9 claim_extractor (every pair by default) | `claim_extractor.py` (flash, M0; `support_sections` stored, never used to read) |
| 1.1 flags to workers / 1.2 verification / 1.4 paged overview / 1.5 overflow batch | `citations/orchestra.py` |
| 1.3 number spellings | `citations/terms.py` |
| 1.6 worker tools via MCP | `citations/access.py` binds `get_citation_sentences` / `search_in_work` (target fields bound by the program) |
| deploy: stepwise citation runs | `citations/runner.py` (start / step, state saved in `citation_runs.state`), `jobs.run_job` |
| deploy: agents on Agent Engine | `agent_handlers.py`, `engines.py`, `serial.py`, `agents/`, `scripts/deploy_agents.py` |
| deploy: ES data migration | `scripts/migrate_es.py` |
| 1.7 incremental re-run | `jobs.rerun_due` (`FIRST_PDATE` since last check, already-judged papers skipped) |
| 1.8 review UI | BFF `apps/bff/review_api.py`; frontend `/review` |
| 1.9 pattern library removed | see git history of this change |
| 1.10 author self-check | `selfcheck.py`; BFF `/api/selfcheck/*`; frontend `/selfcheck` |

## Commands (WSL)

```bash
bash claimdrift/run.sh test                          # offline + MCP tests against the cloud MCP (CLAIMDRIFT_NET=1 adds Europe PMC)
CLAIMDRIFT_LOCAL=1 bash claimdrift/run.sh setup-es   # LOCAL indices (cloud: elastic/scripts/create_indices.py --apply)
bash claimdrift/run.sh analyze incubation_travellers_eurosurv     # layer ①; 'all' = 33 case-bank pairs
bash claimdrift/run.sh citation-worker --once        # layer ② (async; ~7 min per target)
bash claimdrift/run.sh review-queue
bash claimdrift/run.sh review citation '<event_id>::PMC7097845' approved --reviewer jz
bash claimdrift/run.sh notify --dry-run              # drafts; without --dry-run sends only if GMAIL_TOKEN_FILE is set
bash claimdrift/run.sh eval-drift [--route claims]   # P0.2 / P0.9 against gold.json (experiments' scorer)
bash claimdrift/run.sh eval-abstract                 # P0.3 against Brierley (173 test pairs)
bash claimdrift/run.sh score-citations incubation_travellers_eurosurv covid_liver_damage   # P0.5 against citation_gold_v2.json
bash claimdrift/run.sh eval-selfcheck                # P1.10 on the human-verified citing sentences
uv run python apps/bff/server.py   # BFF with review + self-check (cloud ES; CLAIMDRIFT_LOCAL=1 for docker)
```

## Citation gold set

`data/cases/experiments/citation_gold_v2.json` (9 targets): universe = all citing papers with open full
text, material = every citation position incl. full table rows, labelled once by Claude (single annotator, no
adjudication). Guan is a fixed sample of 600. The v1 set (string-prescreen universe, truncated tables) was deleted.

## Decisions made while implementing (not fixed by the design doc)

- `severity_mismatch` review trigger: provisional rule — abstract `no_change` with full-text significant/major, or abstract
  `major` with full-text minor (§3.7 leaves the mapping open). `review.severity_mismatch`.
- Workers may answer `not_relying` (coincidental number / other study); the prototype worker prompt had no such class.
- Every Europe PMC operation of the citation analysis -- pre-screen, `search_more`, `follow_chain`, verification evidence,
  worker reading tools -- goes through `citations/access.py`: in-process locally (`--tools local`) or the MCP tool
  service, which owns the full-text cache (部署方案 A4/T3).
- Citation targets: case-bank papers use `citations/case_targets.json` (same terms as the experiments); other papers derive
  the superseded value from the claim_diff (`terms.old_value_terms`; diffs with root cause wording_only / reporting_choice
  are skipped; changes without a number produce no target — known weak spot, §6).
- Self-check sentence ranking defaults to hybrid ELSER+BM25 with an "<author> et al. <title>" prefix (best Hit@3 in
  `selfcheck_eval.py`); `mode` can be switched to `elser` / `bm25`.
