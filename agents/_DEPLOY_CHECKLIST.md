# Agent Engine deploy checklist

> Distilled from Phase 4b-5/6 (`memory_synthesizer` deploy) — the pitfalls in **§ Pitfalls** below cost ~30 min each the first time. Read them before starting.

For each new agent, work top-to-bottom. Each step is independently verifiable.

---

## Step 1 — Inline `_shared/` imports

The `adk deploy agent_engine` tarball packs **only the agent's own subdirectory**. The sibling `_shared/` package is not importable in the deployed runtime. Server-side `ModuleNotFoundError` surfaces as a generic HTTP 500 with no error body (Pitfall #1).

For each `from _shared.X import Y` in `agent.py`:
- **Constants** (e.g. `MODEL_FLASH`, `MODEL_PRO` from `_shared/config.py`): paste the literal value into `agent.py` with a comment pointing back at the source-of-truth file. Example:
  ```python
  # Inlined from _shared/config.py:MODEL_FLASH. Keep in sync if it ever changes
  # (hasn't since v0).
  MODEL_FLASH = "gemini-2.5-flash"
  ```
- **Code modules** (e.g. `_shared/elastic_retrieval.py`, `_shared/elastic_write.py`): for Phase 5 these should not be imported in the deployed runtime at all — they're being replaced by the Elastic MCP toolset (`McpToolset`). If `agent.py` still imports from them, that's the 5a-style MCP migration; do it before deploy.

Do NOT try to `--extra_packages _shared` across deploys. Each agent ships independently.

`_shared/` stays in-repo because local `adk web` testing of any agent still uses it. Untouched.

## Step 2 — Write a minimal `requirements.txt`

DO NOT run `uv export --no-hashes --format requirements-txt --no-dev`. That pulls in ~80 transitive deps (`aiosqlite` for `adk web` session storage, `alembic`, `authlib`, ...) that inflate the build container and don't serve Agent Engine (Pitfall #2).

Hand-author. The minimum every agent needs:

```
google-adk>=1.0,<2.0
google-cloud-aiplatform[adk,agent-engines]>=1.112
```

Agent Engine's deploy machinery adds `cloudpickle` and `pydantic` on top automatically. Add nothing else unless the agent's `agent.py` actually imports it.

For MCP agents (those using `McpToolset`), the MCP transport is a sub-dep of `google-adk` — no extra entry needed.

Reference: [agents/memory_synthesizer/requirements.txt](memory_synthesizer/requirements.txt).

## Step 3 — Build `.env.deploy` (gitignored)

`adk deploy` reads env vars from `--env_file` and pushes them into the runtime container. Every agent needs at least the two telemetry vars (Pitfall #7 — `--trace_to_cloud` alone does NOT turn these on).

Minimum `.env.deploy` for an agent **without** MCP tools (`notifier`, `claim_extractor`):

```
GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true
```

Minimum `.env.deploy` for an agent **with** MCP tools (`memory_synthesizer`, `drift_analyzer`, `citation_finder`):

```
KIBANA_URL=https://<your-kibana>.cloud.es.io
ELASTIC_API_KEY=<the api key>
GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true
```

**Strip these** even if local `.env` has them (Pitfall #3 — they conflict with `--project` / `--region` flags per [google/adk-python#1185](https://github.com/google/adk-python/issues/1185)):

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_API_KEY`
- Any local-dev-only var

The gitignore pattern `*/.env.deploy` in [agents/.gitignore](.gitignore) covers any `<agent>/.env.deploy` path. Verify with `git check-ignore <agent>/.env.deploy` before continuing. **Never commit the API key.**

## Step 4 — `adk deploy agent_engine`

From the project root in WSL bash (not PowerShell — the ADK CLI's `--project` quoting differs). **Always prefix `uv run`** — bare `adk` will either fail or pick up a wrong Python (project uses uv per `agents/pyproject.toml` + `uv.lock`).

**First deploy (creates a new reasoningEngine resource)**:

```bash
cd ~/claim_drift/agents
uv run adk deploy agent_engine \
  --project=tensile-topic-496519-i1 \
  --region=us-central1 \
  --display_name=<agent_name> \
  --trace_to_cloud \
  --env_file=<agent_name>/.env.deploy \
  --requirements_file=<agent_name>/requirements.txt \
  <agent_name>
```

**Re-deploy in place (e.g. after code change) — must use `--agent_engine_id` to avoid leaving orphan resources**:

```bash
uv run adk deploy agent_engine \
  --project=tensile-topic-496519-i1 \
  --region=us-central1 \
  --agent_engine_id=<numeric_id_from_first_deploy> \
  --display_name=<agent_name> \
  --trace_to_cloud \
  --env_file=<agent_name>/.env.deploy \
  --requirements_file=<agent_name>/requirements.txt \
  <agent_name>
```

The trailing positional arg is the agent directory name (`notifier`, `memory_synthesizer`, etc.), NOT an absolute path. Run from `agents/` so the relative paths in `--env_file` / `--requirements_file` resolve.

Expected output ends with:
```
✅ Created agent engine: projects/.../locations/us-central1/reasoningEngines/<numeric_id>
🎉 View your deployed agent here: https://console.cloud.google.com/.../playground?...
```

**Save the `reasoningEngines/<numeric_id>`** — it goes into the Phase 5f supervisor's sub-agent reference list.

If you get HTTP 500 with empty error body → almost certainly Pitfall #1 (an `_shared/` import slipped through). Search `agent.py` for `from _shared` and inline anything you find.

## Step 5 — E2E smoke test in Vertex Playground

Open the Playground URL from Step 4's output. Submit the agent's canonical test envelope (see § Test envelopes below).

Verify:
- **Tool trace** matches expectation (for MCP agents: the right MCP tool fired with the right params; built-in `platform.*` tools should NOT appear if `tool_filter` is correctly set).
- **Output JSON** matches the §3.x schema for that agent (field names exact; nullable fields nulled; no fabricated DOIs/timestamps).
- For write-side agents (`memory_synthesizer`, write paths): `curl` the ES doc afterwards and confirm the side effect.

Save Playground link + test envelope + actual output as evidence in the Phase 5 changelog entry.

---

## Pitfalls (cost ~30 min each the first time)

1. **`_shared/` import → HTTP 500 with empty body.** Deploy tarball only contains the agent's own subdirectory. Inline all `_shared.X` references into the agent file before deploy. *(Phase 4b-5 found this.)*
2. **`uv export` requirements.txt → fat build container.** Hand-author a 2-line file instead. *(Phase 4b-5 found this.)*
3. **`GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` in `.env.deploy` → conflict with `--project`/`--region` flags.** Strip these from the deploy env file even if your local `.env` has them. *(Phase 4b-5 / adk-python#1185.)*
4. **MCP `Accept` header.** `McpToolset` POST to `/api/agent_builder/mcp` needs `Accept: application/json, text/event-stream` — bare `application/json` returns 406. The ADK toolset sets this automatically when you use `StreamableHTTPConnectionParams`; only matters if you're hand-crafting the HTTP. *(Phase 4b-3 found this.)*
5. **`tool_filter` is required for MCP agents.** Without it the LLM sees ~16 unrelated `platform.*` built-ins from the Elastic MCP server (search, ES|QL, Streams, Cases). Pollutes the tool trace and lets the LLM call out-of-scope tools. Always pass the explicit allowlist. *(Phase 4b-2 design call.)*
6. **`now_iso` / `synthesized_at` / `analyzed_at` should be null in the agent's output.** The agent will sometimes invent a value (often anchored to a date already in context). Leave it for the orchestrator to fill. Documented in §3.5.2 / §3.2.2; mentioned here because it's easy to forget when smoke-testing.

7. **`--trace_to_cloud` alone does NOT populate the Agent Observability dashboard / trace pages / prompt-response content.** Source check (`google/adk-python` `src/google/adk/cli/cli_deploy.py`): `--trace_to_cloud` only sets `enable_tracing=True` on the generated `AdkApp` wrapper — that just emits ADK spans to Cloud Trace. The UI's dashboard + content capture require **two separate env vars** in `.env.deploy`: `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` (runtime OTel export pipeline) and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` (prompt/response body capture; off by default per OTel GenAI semconv). Both env vars are independent — neither flag implies them. *(Phase 5b found this: first notifier deploy used only `--trace_to_cloud` and the Vertex console nagged us to set both env vars and re-deploy.)*

---

## Test envelopes (canonical inputs for each agent)

### `memory_synthesizer` (already deployed — reference)
Feed the §3.5.1-shaped envelope wrapping `demo-drift-001` (the hydroxychloroquine COVID effect-size-reduction event from [elastic/demo_seed/drift_events.json](../elastic/demo_seed/drift_events.json)).

### `notifier`
§3.4.1 envelope: pick an `affected_citation` for the same `demo-drift-001` event (from [elastic/demo_seed/affected_citations.json](../elastic/demo_seed/affected_citations.json) if present, or hand-craft a small one). Expected output: §3.4.2 with `dispatch.status == "drafted"` and 150-300 word body.

### `claim_extractor`
§3.1.1 envelope: one preprint record (abstract + conclusion) from [elastic/demo_seed/preprints.json](../elastic/demo_seed/preprints.json). Expected output: §3.1.2 list of structured claims.

### `drift_analyzer`
§3.2.1 envelope: pair of claim lists (preprint + published) for the demo drift. Expected output: §3.2.2 with `claim_diffs` populated, `retrieved_patterns_used` reflecting Memory Synthesizer's prior writes. Verify tool trace shows `search_drift_patterns` called once.

### `citation_finder`
§3.3.1 envelope: a drift_event. **Until Phase 5d's `openalex_citing_works` MCP tool exists, this agent will fabricate DOIs** — that's the known v0 NOTE in §3.3 and is the reason 5e is blocked on 5d. Once 5d ships, expected output has real OpenAlex DOIs verifiable via [https://api.openalex.org/works/{doi}](https://api.openalex.org/works/doi).

---

## Per-agent Phase 5 status

| Agent | Step 1 (inline _shared) | Step 2 (requirements) | Step 3 (.env.deploy) | Step 4 (deploy) | Step 5 (E2E) | reasoningEngine id |
|---|---|---|---|---|---|---|
| `memory_synthesizer` | ✓ (Phase 4b-5) | ✓ | ✓ | ✓ | ✓ | `8580327609152307200` |
| `notifier` (5b) | ✓ | ✓ | ✓ (telemetry only) | ✓ | ✓ | `7063036747193516032` |
| `claim_extractor` (5c) | ✓ | ✓ | ✓ (telemetry only) | ✓ | ✓ | `2286406392413683712` |
| `drift_analyzer` (5a) | ✓ + MCP-ified | ✓ | ✓ (MCP + telemetry) | ✓ | ✓ | `5333654490283245568` |
| `citation_finder` (5e) | ✓ + MCP-ified | ✓ | ✓ (MCP + telemetry) | ✓ | ✓ | `6997171602643222528` |

Update this table as deploys complete; resource ids feed into Phase 5f supervisor.
