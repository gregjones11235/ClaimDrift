#!/usr/bin/env bash
# Run BFF (http://localhost:8787) + Playground backend (:8799) + frontend (http://localhost:3000); Ctrl-C stops all.
# By default they use the CLOUD system (Elasticsearch Serverless, the MCP service on Cloud Run, the agents on Agent
# Engine; see claimdrift/config.py). `CLAIMDRIFT_LOCAL=1 bash dev.sh` uses the local stack instead (docker claimdrift-es
# on :9200, stdio MCP, agents in-process). Needs: uv, node via nvm, a gcloud login (ID token for the MCP service).
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH" PYTHONIOENCODING=utf-8
export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
trap 'trap - INT TERM EXIT; kill 0' INT TERM EXIT
BFF_PORT=8787 BFF_HOST=0.0.0.0 \
  uv run python apps/bff/server.py &
uv run uvicorn apps.playground.server:app --host 0.0.0.0 --port 8799 &
(cd frontend && npx next dev -H 0.0.0.0 -p 3000) &
wait
