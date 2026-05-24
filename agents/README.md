# ClaimDrift — Agents

> **Status (2026-05-21)**: All 5 agents scaffolded with ADK and pass smoke tests via `adk web`. No tools wired yet; tool calling pipeline (Elastic MCP server) lands next. See [v0 status / known limitations](#v0-status--known-limitations) below.

This subdirectory contains the 5 Gemini agents that power ClaimDrift, built on top of [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/). For the project-wide overview, see the root `README.md`. For cross-component contracts (Elasticsearch indices, SSE event format, agent invocation order), see `../docs/contracts.md`.

## Agents

| Agent | Model | Purpose | Writes to (ES index) |
|---|---|---|---|
| `claim_extractor` | `gemini-2.5-flash` | Decomposes preprint/published-paper text into structured claims | `claims` |
| `drift_analyzer` | `gemini-2.5-pro` | Diffs preprint-final ↔ published claim sets; produces drift report (reads `drift_patterns` for memory loop) | `drift_events` |
| `citation_finder` | `gemini-2.5-flash` | Finds downstream papers citing a drifted preprint, scores severity | `affected_citations` |
| `notifier` | `gemini-2.5-flash` | Drafts and dispatches notification emails per affected citation | `notification_log` |
| `memory_synthesizer` | `gemini-2.5-pro` | Distills drift events into reusable patterns (async loop) | `drift_patterns` |

> **Model note**: `gemini-3.5-flash` was released two days before v0 scaffolding but is not yet reachable via ADK in `us-central1` for our project. We will revisit the model assignments before submission. See `../docs/contracts.md` Changelog (2026-05-21).

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
ELASTIC_ENDPOINT=https://...
ELASTIC_API_KEY=...
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

```bash
uv run python scripts/test_es_connection.py
```

The `uv run` prefix tells uv to execute the command inside the project's `.venv` automatically — no manual activation required.

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

> **Tools directory**: Per-agent `tools/` subdirectories are not yet created. They will be added in the next step when wiring the Elastic MCP server (for `drift_analyzer`, `citation_finder`, `memory_synthesizer`). `claim_extractor` and `notifier` may never need tools.

## v0 status / known limitations

All 5 agents pass smoke tests as of 2026-05-21. The items below are tracked for the v1 prompt-iteration pass (owned by A) and the tool-wiring pass (owned by C). None block other workstreams from making progress.

### Prompt-iteration items (→ A)

| Agent | Finding | Required fix |
|---|---|---|
| `claim_extractor` | `numerical_values[*].comparison` occasionally returns `null` when sentence has reduction/increase verbs | Prompt must enforce non-null when such verbs are present |
| `drift_analyzer` | Pro model spontaneously detected scope-narrowing drift but had to shoehorn into `hedging_added` | Propose adding `scope_restricted` to §3.2.2 `diff_type` enum (pending team discussion per §8.1) |
| `citation_finder` 🚨 | v0 fabricates realistic-looking DOIs using real journal prefixes (`10.1038/...`, `10.1016/...`) | Until the agent uses the real OpenAlex tool, all v0 fabricated outputs must use sentinel DOIs (`10.0000/synthetic-v0-NNN`) or set `citation_context = "SYNTHETIC_V0_PLACEHOLDER"`. **v0 fabricated output must not be persisted to ES.** B's OpenAlex utility may persist real citing-work candidates separately as `record_source=openalex_candidate`, `severity_tier=pending`. |
| `notifier` | Quoted phrase fragments ("reduced viral load by 45%") instead of full sentences | Prompt must enforce full-sentence quoting (subject + verb + object + modifiers) |
| `memory_synthesizer` | Only 2 `domain_tags` returned, contracts.md example shows 3-5 | Prompt should encourage 3-6 tags mixing general + specific |

### Tool-wiring items (→ C, next step)

- No agent has tools wired yet. v0 inputs are passed as JSON in the chat message.
- Next: validate ADK tool calling end-to-end with a Python function tool (Step B).
- After that: replace function tools with Elastic MCP server tools for the agents that need ES retrieval (Step C).

## Deploying to Cloud Run

(WIP — to be filled in once the first agent is end-to-end functional.)

Outline:
1. `uv export --no-hashes -o requirements.txt` (Cloud Build buildpacks read `requirements.txt`)
2. `gcloud run deploy <agent-name> --source . --region us-central1`

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
