"""Deploy (or update in place) the five ClaimDrift agents on Vertex AI Agent Engine (部署方案 §4.4, A5).

Run from the repo root in WSL:
  uv run python claimdrift/scripts/deploy_agents.py \
      --project gen-lang-client-0220563082 --location us-central1 \
      --staging-bucket gs://gen-lang-client-0220563082-agent-staging --mcp-url https://<mcp-service>.run.app/mcp

  --only notifier,drift_analyzer      redeploy a subset (the supervisor is redeployed last whenever it is included)

Each engine is an AdkApp around agents.common.FunctionAgent; `claimdrift/` and `agents/` travel as extra packages
(the old system could not import shared code from a sibling directory; extra_packages fixes that). The Gemini API key
comes from Secret Manager (secret gemini-api-key); the agents do not touch Elasticsearch, so they get no ES key.

Engine resource names are written to agents/engines.json and kept stable: an engine that is already listed there is
UPDATED, never re-created. Every engine's description records the git commit it was built from (risk 2: the engines run
a snapshot of the code taken at deploy time -- check this before debugging a demo).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ENGINES_FILE = ROOT / "agents" / "engines.json"
SUBAGENTS = ("claim_extractor", "drift_analyzer", "citation_finder", "notifier")
USES_MCP = {"citation_finder", "supervisor"}
# Same versions as the local environment (repo-root pyproject.toml / uv.lock): the AdkApp is pickled locally and unpickled in the engine.
REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent-engines]==1.153.1",
    "google-adk==1.34.0",
    "google-genai==1.75.0",
    "mcp==1.27.1",
    "httpx==0.28.1",
    "google-auth==2.53.0",
    "cloudpickle==3.1.2",
    "pydantic==2.13.4",
    # Agent Engine telemetry: without these the traces show the ADK spans but not the Gemini / HTTP calls inside them
    "opentelemetry-instrumentation-google-genai",
    "opentelemetry-instrumentation-httpx",
]


def git_commit() -> str:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "claimdrift", "agents"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


def clean_pycache() -> None:
    for d in ("claimdrift", "agents"):
        for p in (ROOT / d).rglob("__pycache__"):
            shutil.rmtree(p, ignore_errors=True)


def env_for(name: str, mcp_url: str, engines: dict, commit: str) -> dict:
    from google.cloud.aiplatform_v1.types import env_var
    env = {"GEMINI_API_KEY": env_var.SecretRef(secret="gemini-api-key", version="latest"),
           "CLAIMDRIFT_GIT_COMMIT": commit,
           "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
           "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "true"}
    if name in USES_MCP:
        env["CLAIMDRIFT_MCP_URL"] = mcp_url
    if name == "supervisor":
        env["CLAIMDRIFT_ENGINES"] = json.dumps({k: engines[k]["resource_name"] for k in SUBAGENTS})
    return env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="us-central1")
    ap.add_argument("--staging-bucket", required=True)
    ap.add_argument("--mcp-url", required=True, help="the MCP service URL including /mcp")
    ap.add_argument("--only", default="", help="comma-separated subset")
    a = ap.parse_args()

    import vertexai
    from vertexai import agent_engines
    from agents.common import DESCRIPTIONS, app

    vertexai.init(project=a.project, location=a.location, staging_bucket=a.staging_bucket)
    engines = json.loads(ENGINES_FILE.read_text(encoding="utf-8")) if ENGINES_FILE.exists() else {}
    names = [n for n in (*SUBAGENTS, "supervisor") if not a.only or n in a.only.split(",")]
    commit = git_commit()
    clean_pycache()
    for name in names:
        if name == "supervisor" and not all(k in engines for k in SUBAGENTS):
            raise SystemExit("deploy the four sub-agents before the supervisor")
        kw = dict(agent_engine=app(name), requirements=REQUIREMENTS, extra_packages=["claimdrift", "agents"],
                  env_vars=env_for(name, a.mcp_url, engines, commit), display_name=f"claimdrift-{name}",
                  description=f"{DESCRIPTIONS[name]} [commit {commit}]")
        t0 = dt.datetime.now()
        if name in engines:
            print(f"updating {name} ({engines[name]['resource_name']}) ...", flush=True)
            eng = agent_engines.update(resource_name=engines[name]["resource_name"], **kw)
        else:
            print(f"creating {name} ...", flush=True)
            eng = agent_engines.create(**kw)
        engines[name] = {"resource_name": eng.resource_name, "commit": commit,
                         "deployed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        ENGINES_FILE.write_text(json.dumps(engines, indent=1) + "\n", encoding="utf-8")
        print(f"  {name}: {eng.resource_name} ({(dt.datetime.now() - t0).seconds}s)", flush=True)
    print(json.dumps(engines, indent=1))


if __name__ == "__main__":
    main()
