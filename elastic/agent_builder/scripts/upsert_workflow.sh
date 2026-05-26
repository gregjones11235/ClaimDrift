#!/usr/bin/env bash
# Idempotent upsert for an Elastic Workflow YAML definition.
#
# Usage:
#   ./upsert_workflow.sh <path/to/workflow.yaml>
#
# Derives the workflow id from the YAML's `name:` field (snake_case →
# kebab-case, the slug rule Kibana applies internally — see Phase 4a-3
# 2026-05-23 changelog "Slug conversion gotcha"). POSTs to
# /api/workflows with overwrite=true: single endpoint handles both
# create and update, no list-then-branch needed.
#
# Configuration: reads agents/.env at the repo root (same file used by
# upsert_tool.sh and the Python code). Required keys:
#   ELASTIC_API_KEY   Same key that works for /api/agent_builder/tools.
#   KIBANA_URL        Kibana host for the serverless project (the `.kb.`
#                     host, NOT the `.es.` host).
#
# Why a shell script with curl + jq: the bulk POST endpoint wants
# {workflows:[{id, yaml}]}, where `yaml` is a string field carrying the
# YAML source verbatim. jq -Rs is the cleanest way to slurp the YAML
# file into a JSON string literal without worrying about escaping.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <path/to/workflow.yaml>" >&2
  exit 64
fi

workflow_file="$1"
if [[ ! -f "$workflow_file" ]]; then
  echo "workflow file not found: $workflow_file" >&2
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

# Derive workflow id from the YAML `name:` line. Apply the snake→kebab
# slug rule Kibana uses internally.
yaml_name="$(grep -E '^name:' "$workflow_file" | head -1 | sed -E 's/^name:[[:space:]]*//; s/[[:space:]]+$//')"
if [[ -z "$yaml_name" ]]; then
  echo "could not find 'name:' field in $workflow_file" >&2
  exit 65
fi
workflow_id="$(echo "$yaml_name" | tr '_' '-')"

echo "→ upserting workflow id=${workflow_id} (yaml name=${yaml_name}) from $workflow_file"

# Build the bulk-POST envelope with jq -Rs to inline the YAML safely.
body="$(jq -Rs --arg id "$workflow_id" '{workflows: [{id: $id, yaml: .}]}' < "$workflow_file")"

curl -s -X POST "${KIBANA_URL%/}/api/workflows?overwrite=true" \
  -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
  -H "Content-Type: application/json" \
  -H "kbn-xsrf: true" \
  -H "x-elastic-internal-origin: Kibana" \
  --data-binary "$body" | python3 -m json.tool
