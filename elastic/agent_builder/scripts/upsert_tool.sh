#!/usr/bin/env bash
# Idempotent upsert for an Elastic Agent Builder tool definition.
#
# Usage:
#   ./upsert_tool.sh <path/to/tool.json>
#
# Reads tool id from the JSON's `id` field. If a tool with that id already
# exists, PUTs (update). Otherwise POSTs (create).
#
# Configuration: reads agents/.env at the repo root (same file the Python
# code uses). Required keys:
#   ELASTIC_API_KEY   API key with feature_agentBuilder.{read,write} +
#                     cluster monitor_inference. The ingestion key already
#                     in .env may or may not have the agentBuilder Kibana
#                     features — if upsert returns 403, create a new key
#                     with broader scope in Kibana → Stack Management →
#                     API keys.
#   KIBANA_URL        Kibana host for the serverless project. NOTE: this
#                     is the `.kb.` host, NOT the `.es.` host. Find it in
#                     your Kibana browser URL bar.
#
# Why a shell script and not a Python CLI: the Agent Builder tool
# definitions live in a single small JSON each. A wrapped curl keeps the
# round-trip visible — useful when debugging serverless API differences.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <path/to/tool.json>" >&2
  exit 64
fi

tool_file="$1"
if [[ ! -f "$tool_file" ]]; then
  echo "tool file not found: $tool_file" >&2
  exit 66
fi

# Load agents/.env relative to this script.
script_dir="$(cd "$(dirname "$0")" && pwd)"
env_file="${script_dir}/../../../agents/.env"
if [[ -f "$env_file" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$env_file"; set +a
fi

: "${ELASTIC_API_KEY:?ELASTIC_API_KEY must be set in agents/.env}"
: "${KIBANA_URL:?KIBANA_URL must be set in agents/.env (the .kb. host, not .es.)}"

tool_id="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['id'])" "$tool_file")"
if [[ -z "$tool_id" ]]; then
  echo "could not read 'id' from $tool_file" >&2
  exit 65
fi

base="${KIBANA_URL%/}/api/agent_builder/tools"

http_status() {
  curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
    -H "kbn-xsrf: claimdrift" \
    "$1"
}

existing="$(http_status "${base}/${tool_id}")"

if [[ "$existing" == "200" ]]; then
  echo "→ tool ${tool_id} exists, updating (PUT)"
  curl -s -X PUT "${base}/${tool_id}" \
    -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
    -H "Content-Type: application/json" \
    -H "kbn-xsrf: claimdrift" \
    --data-binary @"$tool_file" | python3 -m json.tool
elif [[ "$existing" == "404" ]]; then
  echo "→ tool ${tool_id} does not exist, creating (POST)"
  curl -s -X POST "$base" \
    -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
    -H "Content-Type: application/json" \
    -H "kbn-xsrf: claimdrift" \
    --data-binary @"$tool_file" | python3 -m json.tool
else
  echo "unexpected status checking ${tool_id}: $existing" >&2
  exit 1
fi
