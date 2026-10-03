"""Runtime configuration of the ClaimDrift system (新系统改造设计.md §0, 新系统云端部署.md).

DEFAULT = THE CLOUD DEPLOYMENT. Without any CLAIMDRIFT_* variable a command talks to the deployed system:
  Elasticsearch   Elasticsearch Serverless (ELASTIC_ENDPOINT / ELASTIC_API_KEY in .env), ELSER .elser-2-elastic
  MCP tools       the Cloud Run service claimdrift-mcp (Google ID token)
  agents          the five engines on Vertex AI Agent Engine listed in agents/engines.json
CLAIMDRIFT_LOCAL=1 switches to the local development stack: docker Elasticsearch on localhost:9200 (ELSER elser-local),
a stdio MCP server started by each client, every agent in-process. Each CLAIMDRIFT_* variable below still overrides
its own setting in either mode.

Constraints encoded here:
  - Gemini through the AI Studio API key (GEMINI_API_KEY), also inside the engines: GOOGLE_GENAI_USE_VERTEXAI is forced
    to false.
  - drift_analyzer and the citation orchestra run on pro; flash is used ONLY by claim_extractor and the notifier.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader (.env is gitignored: GEMINI_API_KEY, ELASTIC_*). Never overrides the environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"

LOCAL = os.environ.get("CLAIMDRIFT_LOCAL", "").lower() in ("1", "true", "yes")

# Data root: the repo's data/ on a developer machine (a copy of the bucket); the Cloud Storage bucket mounted at /data
# on Cloud Run (CLAIMDRIFT_DATA=/data).
DATA_DIR = Path(os.environ.get("CLAIMDRIFT_DATA", str(ROOT / "data")))
CASES_DIR = DATA_DIR / "cases"
EXPERIMENTS_DIR = CASES_DIR / "experiments"
# Fetched JATS for pairs outside the case bank (same layout as a case dir: preprint_v1.xml, published_*.xml)
DOCSTORE_DIR = Path(os.environ.get("CLAIMDRIFT_DOCSTORE", str(DATA_DIR / "docstore")))
EPMC_CACHE_DIR = Path(os.environ.get("CLAIMDRIFT_EPMC_CACHE", str(EXPERIMENTS_DIR / "_cache_epmc")))
EPMC_MISSING_DAYS = 30  # a manual `<id>.missing` record in EPMC_CACHE_DIR suppresses that request for this many days
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# MCP tool service: the deployed Cloud Run service (called with a Google ID token); local mode -> each client starts
# `python -m claimdrift.mcp_server` over stdio.
CLOUD_MCP_URL = "https://claimdrift-mcp-648991158366.us-central1.run.app/mcp"
MCP_URL = os.environ.get("CLAIMDRIFT_MCP_URL") or (None if LOCAL else CLOUD_MCP_URL)
# Vertex AI Agent Engine: the resource names of the five agents, as JSON {"supervisor": "projects/.../reasoningEngines/
# <id>", ...} or the path of such a file (agents/engines.json, written by claimdrift/scripts/deploy_agents.py).
# Local mode (or no engines file) -> every agent runs in-process.
_ENGINES_FILE = ROOT / "agents" / "engines.json"
ENGINES_SPEC = os.environ.get("CLAIMDRIFT_ENGINES") or (str(_ENGINES_FILE) if not LOCAL and _ENGINES_FILE.exists() else None)

MODEL_PRO = os.environ.get("CLAIMDRIFT_MODEL_PRO", "gemini-3.1-pro-preview")
MODEL_FLASH = os.environ.get("CLAIMDRIFT_MODEL_FLASH", "gemini-3.8-flash")

if LOCAL:
    ES_ENDPOINT = os.environ.get("CLAIMDRIFT_ES", "http://localhost:9200").rstrip("/")
    ES_API_KEY = os.environ.get("CLAIMDRIFT_ES_API_KEY") or None
    ELSER_INFERENCE_ID = os.environ.get("CLAIMDRIFT_ELSER", "elser-local")
else:
    ES_ENDPOINT = (os.environ.get("CLAIMDRIFT_ES") or os.environ.get("ELASTIC_ENDPOINT") or "").rstrip("/")
    ES_API_KEY = os.environ.get("CLAIMDRIFT_ES_API_KEY") or os.environ.get("ELASTIC_API_KEY") or None
    ELSER_INFERENCE_ID = os.environ.get("CLAIMDRIFT_ELSER", ".elser-2-elastic")

# drift_analyzer routing (§3.3). Decision 2026-10-02: every pair goes through claim_extractor ("claims", the 5-agent
# flow) regardless of length. "auto" restores the old length rule: <= STUFF_LIMIT_TOKENS -> one stuffed call.
DEFAULT_ROUTE = os.environ.get("CLAIMDRIFT_ROUTE", "claims")
STUFF_LIMIT_TOKENS = int(os.environ.get("CLAIMDRIFT_STUFF_LIMIT", "100000"))
PROMPT_VERSION = "v4a"

# claim_extractor (§3.3.1)
EXTRACT_CHUNK_CHARS = 24_000
EXTRACT_WORKERS = 4

# citation orchestra caps (§3.5)
ORCH_MAX_WORKERS, ORCH_MAX_PAPERS, ORCH_MAX_ROUNDS, ORCH_MAX_HOPS = 8, 5, 3, 2
ORCH_BUDGET = 14
OVERVIEW_PAGE_SIZE = 40   # P1.4: candidates per overview() page (the 12k-char tool-result cap hid everything past ~45)
PREFETCH_WORKERS = 8
# Large targets (Guan: ~19k citing papers, 240 with the old value in their full text): the pre-screen downloads full
# texts in batches, one batch per step, so no single call nears Agent Engine's request limit. Best effort up to
# PRESCREEN_MAX_WORKS per run; anything beyond is NOT screened and the run is reported as truncated (never silently).
PRESCREEN_BATCH = 20  # ~40-60 s per step on a cold cache, so progress is reported often
PRESCREEN_MAX_WORKS = 2000

# notifier (§3.8): this is not a commercial product -- every notification goes to the project's test inbox,
# unconditionally. Real author e-mail addresses are never extracted or stored.
NOTIFY_OVERRIDE_EMAIL = os.environ.get("NOTIFY_OVERRIDE_EMAIL", "claimdriftnotifier@gmail.com")
# Gmail API OAuth token of the sender account (claimdrift/scripts/gmail_oauth_local.py); unset -> drafts only
GMAIL_TOKEN_FILE = os.environ.get("GMAIL_TOKEN_FILE")

INDICES = {
    "preprints": "preprints",
    "drift_events": "drift_events",
    "claims": "claims",
    "affected_citations": "affected_citations",
    "citation_runs": "citation_runs",
    "notification_log": "notification_log",
}
