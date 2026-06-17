# ClaimDrift — Agents

> **Status (2026-05-29)**: 5/5 sub-agents + 1 supervisor deployed to Vertex AI Agent Engine. Memory loop closed (`memory_synthesizer` retrieves + updates patterns via Elastic MCP). Main §4.1 fan-out wired through supervisor + Cloud Run dispatcher, self-driven by the Elastic Scheduled Workflow. T1 real N>0 end-to-end verified on `10.1101/2024.05.03.24306688` (4 affected citations, 4 sent emails). BFF + Next.js frontend now live on real data. See `../docs/contracts.md` changelog for the full deployment trail.

This subdirectory contains the 5 sub-agents + supervisor that power ClaimDrift, built on top of [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/). For the project-wide overview, see the root `README.md`. For cross-component contracts (Elasticsearch indices, SSE event format, agent invocation order), see `../docs/contracts.md`. For per-agent deploy gotchas accumulated over Phases 4-5, see `_DEPLOY_CHECKLIST.md`.

## Agents

| Agent | Model | Purpose | Writes to (ES index) |
|---|---|---|---|
| `claim_extractor` | `gemini-2.5-flash` | Decomposes preprint/published-paper text into structured claims | `claims` |
| `drift_analyzer` | `gemini-2.5-pro` | Diffs preprint-final ↔ published claim sets; produces drift report (reads `drift_patterns` for memory loop) | `drift_events` |
| `citation_finder` | `gemini-2.5-flash` | Finds downstream papers citing a drifted preprint, scores severity | `affected_citations` |
| `notifier` | `gemini-2.5-flash` | Drafts and dispatches notification emails per affected citation | `notification_log` |
| `memory_synthesizer` | `gemini-2.5-pro` | Distills drift events into reusable patterns (async loop) | `drift_patterns` |

> **Model note**: the agents run on `gemini-2.5-flash` / `gemini-2.5-pro` (centralized in `_shared/config.py`). `gemini-3.5-flash`, released shortly before v0 scaffolding, was evaluated but not adopted — it was not reachable via ADK in `us-central1` for our project at scaffolding time. See `../docs/contracts.md` Changelog (2026-05-21).

## Prerequisites

- **OS**: Linux, macOS, or WSL2 on Windows. Native Windows is not tested.
- **uv** ≥ 0.4 (Python package + project manager; installs Python for you, no system Python needed)
- **Google Cloud SDK (`gcloud`)** with an authenticated account
- **A GCP project** with billing enabled and the following APIs turned on:
  - `aiplatform.googleapis.com` (Vertex AI / Gemini)
  - `discoveryengine.googleapis.com` (Agent Builder backend)
  - `run.googleapis.com` (Cloud Run)
  - `cloudbuild.googleapis.com`
  - `artifactregistry.googleapis.com`

## One-time setup

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

Verify:
```bash
uv --version
```

### 2. Install Google Cloud SDK

```bash
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
gcloud init
gcloud auth application-default login
```

Confirm you are pointing at the right project:
```bash
gcloud config get-value project
# Should print the hackathon project ID
```

Enable required APIs:
```bash
gcloud services enable \
  aiplatform.googleapis.com \
  discoveryengine.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
```

### 3. Set up the project Python environment

From this `agents/` directory:

```bash
# Install Python 3.12 (uv-managed, isolated from system Python)
uv python install 3.12

# Sync dependencies (creates .venv/ and installs everything from uv.lock)
uv sync
```

`uv sync` reads `pyproject.toml` + `uv.lock` and builds a reproducible virtual environment in `.venv/`. You do **not** need to manually create a venv or activate it.

### 4. Configure environment variables

Copy the example env file and fill in the blanks:

```bash
cp .env.example .env
```

`.env` must contain at minimum:
```
GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_GENAI_USE_VERTEXAI=TRUE

# Elastic connection (provided by B)
ELASTIC_ENDPOINT=https://...   # the .es. (data-plane) host
ELASTIC_API_KEY=...
KIBANA_URL=https://...         # the .kb. host — needed by the Agent Builder upsert scripts
```

`.env` is gitignored. Never commit it.

## Daily workflow

### Run an agent locally with the ADK dev UI

```bash
uv run adk web
```

Opens `http://localhost:8000` with an interactive chat UI. Pick an agent from the top-left dropdown and send a message. This is the fastest way to iterate on prompts and tool wiring.

### Run an agent headless (one-shot)

```bash
uv run adk run claim_extractor
```

### Run an arbitrary Python script

The `uv run` prefix runs any command inside the project's `.venv` — no manual activation needed. The only prerequisite at this point is `uv sync` (above); this example has no other dependencies:

```bash
uv run python -c "import google.adk; print('adk', google.adk.__version__)"
```

The diagnostic scripts under `scripts/` (e.g. `diagnose_retrieval_stack.py`, `probe_rrf_scores.py`) run the same way but are **not** zero-setup: they query the live `drift_patterns_read` alias, so they only work after the Elasticsearch indices + alias + data are in place (root README steps 1 and 1b), with `agents/.env` filled in.

### (Optional) Activate the venv the conda-style way

If you prefer to drop the `uv run` prefix:

```bash
source .venv/bin/activate
adk web
deactivate
```

This is fine, but the `uv run` style is preferred because it never gets out of sync with the lockfile.

## Adding / removing dependencies

**Always use `uv add` / `uv remove`, never `pip install`.** uv keeps `pyproject.toml` and `uv.lock` in sync; raw pip will desynchronize them.

```bash
uv add elasticsearch                  # add a runtime dependency
uv add --dev pytest ruff              # add a dev-only dependency
uv remove some-package                # remove
uv lock --upgrade                     # refresh the lockfile to latest compatible versions
```

After any dependency change, commit both `pyproject.toml` and `uv.lock`.

## Project layout

```
agents/
├── .venv/                    # uv-managed virtualenv (gitignored)
├── pyproject.toml            # project metadata + declared dependencies
├── uv.lock                   # exact resolved versions (commit this)
├── .python-version           # pins Python 3.12
├── .env.example              # template for required env vars
├── .env                      # local secrets (gitignored)
├── main.py                   # legacy Vertex AI smoke test (kept for diagnostics)
├── _shared/                  # cross-agent config (model ids, constants)
│   ├── __init__.py
│   └── config.py
├── claim_extractor/          # ADK agent: claim extraction
│   ├── __init__.py
│   └── agent.py              # exports root_agent (LlmAgent)
├── drift_analyzer/           # ADK agent: drift detection (memory loop read side)
├── citation_finder/
├── notifier/
├── memory_synthesizer/       # ADK agent: memory loop write side
└── README.md                 # this file
```

Each agent directory is independently discoverable by `adk web` / `adk run` via the `name=` argument on its `LlmAgent`. The `_shared/` package will also appear in the `adk web` dropdown but is not an agent — do not select it.

> **Tools**: there are no per-agent `tools/` subdirectories. The Elastic tools (`search_drift_patterns`, `create_drift_pattern`, `update_drift_pattern`, `openalex_citing_works`) are exposed through the Agent Builder MCP server and wired in-agent via ADK's `McpToolset` with a `tool_filter` (see `drift_analyzer` / `citation_finder` / `memory_synthesizer` `agent.py`). `claim_extractor` and `notifier` use no tools (pure LLM draft).

## Deployed status

All 6 ADK packages (5 sub-agents + supervisor) ship as separate Vertex AI Agent Engine reasoningEngines:

| Agent | reasoningEngine id | Notes |
|---|---|---|
| `claim_extractor` | `2286406392413683712` | Phase 5c |
| `drift_analyzer` | `5333654490283245568` | Phase 5a; calls `search_drift_patterns` MCP tool |
| `citation_finder` | `6997171602643222528` | Phase 5e; calls `openalex_citing_works` MCP tool |
| `notifier` | `7063036747193516032` | Phase 5b; no tools (pure draft) |
| `memory_synthesizer` | `8580327609152307200` | Phase 4b; calls `search_drift_patterns` + `create_drift_pattern` + `update_drift_pattern` |
| `supervisor_agent` | `7816826734824652800` | Phase 5f-i; custom `BaseAgent` orchestrating §4.1 fan-out across the 5 above |

Deploy-time pitfalls (every one cost ~30 min the first time encountered) are codified in [`_DEPLOY_CHECKLIST.md`](_DEPLOY_CHECKLIST.md) — `_shared/` import inlining, hand-authored `requirements.txt`, `.env.deploy` telemetry vars, MCP `Accept` header, `tool_filter` requirement, cross-reasoning-engine IAM grants, etc. Read that file before deploying a new agent.

For the §4.1 main-flow trigger + persistence layer, see [`../apps/dispatcher/`](../apps/dispatcher/).

## Open prompt-iteration items (→ A)

Prompts currently live inlined in each agent's `agent.py`. As prompts get iterated, the §3.x findings below from earlier rounds may or may not still apply. Verify against current behavior before fixing:

| Agent | Original v0 finding | Status |
|---|---|---|
| `claim_extractor` | `numerical_values[*].comparison` occasionally returns `null` when sentence has reduction/increase verbs | Worth re-verifying against current Gemini 2.5 |
| `drift_analyzer` | Spontaneously detects scope-narrowing drift but shoehorns into `hedging_added` | Pending §8.1 discussion to add `scope_restricted` to `diff_type` enum |
| `notifier` | Quoted phrase fragments instead of full sentences | T1 round-3 inspection shows full sentences are now used; likely resolved |
| `memory_synthesizer` | Only 2 `domain_tags` returned; contracts example shows 3-5 | Worth a re-check after T2 produces more drift_patterns |

## Troubleshooting

### `uv add` fails with a long dependency-resolution error

Skip the middle of the error message — it's just uv listing every version it tried. Read the first line (`× No solution found ...`) and the last 1-2 lines (`And because ... your project's requirements are unsatisfiable.`) to find the actual conflict.

### `gcloud` says "credentials not found" when running an agent

You need Application Default Credentials, not just login:
```bash
gcloud auth application-default login
```

### Agent runs locally but fails in Cloud Run with permission errors

The Cloud Run service account needs:
- `roles/aiplatform.user` (call Gemini)
- `roles/run.invoker` (if called from another service)

Add via:
```bash
gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
  --member="serviceAccount:<sa-email>" \
  --role="roles/aiplatform.user"
```

### `(base)` prefix keeps appearing in my shell

That's conda auto-activating. Disable with:
```bash
conda config --set auto_activate_base false
```
Then restart your terminal. This doesn't uninstall conda; it just stops auto-activation.

## Versions pinned by this project

- Python: 3.12
- google-adk: 1.x (we do not yet target ADK 2.0 Beta — `google-cloud-aiplatform[adk]` does not support 2.0 as of this writing)
- google-cloud-aiplatform: ≥ 1.112 with `[agent_engines,adk]` extras
- Gemini models: `gemini-2.5-flash` (claim_extractor, citation_finder, notifier) and `gemini-2.5-pro` (drift_analyzer, memory_synthesizer). Centralized in `_shared/config.py`; change in one place to swap.

If you need to verify ADK is correctly installed:
```bash
uv run python -c "import google.adk; print(google.adk.__version__)"
```