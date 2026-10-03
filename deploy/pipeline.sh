#!/usr/bin/env bash
# Automatic data pipeline (新系统云端部署.md §4.6): pullers -> preprints -> Elastic Workflow -> dispatcher -> Pub/Sub
# -> /run -> claimdrift.pipeline (supervisor on Agent Engine) -> drift_events + queued citation runs.
#
#   bash deploy/pipeline.sh deploy     build + deploy everything; starts PAUSED (budget 0, schedules paused, workflow off)
#   bash deploy/pipeline.sh batch N    pull and analyse ONE batch of N pairs: watermark rewound to the oldest pair
#                                      without an event, budget N, workflow on, pullers run now and hourly; when N pairs
#                                      have been dispatched the dispatcher pauses everything by itself
#   bash deploy/pipeline.sh pause      stop now (budget closed, puller schedules paused, workflow disabled)
#   bash deploy/pipeline.sh status     budget, schedules, workflow, the last pipeline events
#
# While paused nothing runs: no pull, no workflow run (so no failed-run alerts), the dispatcher has no minimum instance.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . .env; set +a
PROJECT=gen-lang-client-0220563082; REGION=us-central1; REPO=claimdrift
PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
BUCKET=${PROJECT}-claimdrift-data; SA=claimdrift-run@$PROJECT.iam.gserviceaccount.com
IMG=$REGION-docker.pkg.dev/$PROJECT/$REPO
KB=$(echo "$ELASTIC_ENDPOINT" | sed 's/\.es\./.kb./; s/:443$//')   # Kibana of the same Serverless project
PULLERS=(claimdrift-biorxiv-puller claimdrift-medrxiv-puller claimdrift-crossref-puller)
SCHEDULES=(claimdrift-biorxiv-hourly claimdrift-medrxiv-hourly claimdrift-crossref-hourly)
PULL_SINCE=${PULL_SINCE:-2025-10-01}   # preprints posted since then, most of which have been published by now
STEP=${1:-status}
es() { curl -s -H "Authorization: ApiKey $ELASTIC_API_KEY" -H "Content-Type: application/json" "$@"; }
url() { gcloud run services describe "$1" --region=$REGION --format='value(status.url)' 2>/dev/null; }
set_budget() {  # $1 = max
  es -X PUT "$ELASTIC_ENDPOINT/dispatch_state/_doc/dispatch_budget?refresh=wait_for" -d "{\"flow_name\": \"dispatch_budget\",
    \"budget_max\": $1, \"budget_used\": 0, \"analyzed_pairs\": [], \"budget_started_at\": \"$(date -u +%FT%TZ)\",
    \"last_updated_at\": \"$(date -u +%FT%TZ)\"}" >/dev/null
}
schedules() { for s in "${SCHEDULES[@]}"; do gcloud scheduler jobs "$1" "$s" --location=$REGION --quiet >/dev/null 2>&1 || true; done; }
KH=(-H "Authorization: ApiKey $ELASTIC_API_KEY" -H "kbn-xsrf: true" -H "x-elastic-internal-origin: Kibana" -H "Content-Type: application/json")
upload_workflow() {  # stdin = workflow YAML
  jq -Rs '{workflows: [{id: "dispatch-new-pairs", yaml: .}]}' | curl -s -X POST "${KH[@]}" "$KB/api/workflows?overwrite=true" --data-binary @- \
    | jq -c '{uploaded: ([.created[]?.id] + [.updated[]?.id]), failed}'
}
workflow_enabled() {  # $1 = true|false; Kibana has no toggle endpoint: re-upload the stored YAML with `enabled:` flipped
  curl -s "${KH[@]}" "$KB/api/workflows?size=100" | jq -r '.results[] | select(.id=="dispatch-new-pairs") | .yaml' \
    | sed -E "s/^enabled: (true|false)\$/enabled: $1/" | upload_workflow
}

if [[ $STEP == deploy ]]; then
  gcloud services enable pubsub.googleapis.com --quiet
  uv run python -m claimdrift setup-es | grep -E "preprints|dispatch_state" -A2 || true
  # workflow -> dispatcher bearer token (Elastic Workflows has no secret store: it is also stored in the workflow)
  gcloud secrets describe wf-bearer-token >/dev/null 2>&1 || \
    openssl rand -hex 32 | tr -d '\n' | gcloud secrets create wf-bearer-token --data-file=- --quiet
  gcloud secrets add-iam-policy-binding wf-bearer-token --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor --condition=None --quiet >/dev/null
  # the dispatcher pauses the puller schedules once its budget is used up
  gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/cloudscheduler.admin --condition=None --quiet >/dev/null
  # Pub/Sub signs push OIDC tokens as $SA
  gcloud iam service-accounts add-iam-policy-binding $SA --member=serviceAccount:service-$PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com \
    --role=roles/iam.serviceAccountTokenCreator --quiet >/dev/null

  # --- pullers (Cloud Run Jobs) + hourly schedules, created paused
  gcloud builds submit . --config=ingestion/cloudbuild.yaml --substitutions=_IMAGE=$IMG/ingestion-puller:latest --quiet | tail -1
  for src in biorxiv medrxiv; do
    gcloud run jobs deploy claimdrift-$src-puller --image=$IMG/ingestion-puller:latest --region=$REGION --service-account=$SA \
      --args=--source=$src,--since=$PULL_SINCE,--limit=300,--include-published,--apply \
      --set-env-vars=ELASTIC_ENDPOINT=$ELASTIC_ENDPOINT --set-secrets=ELASTIC_API_KEY=es-api-key:latest \
      --task-timeout=1800 --max-retries=0 --quiet | tail -1
  done
  gcloud run jobs deploy claimdrift-crossref-puller --image=$IMG/ingestion-puller:latest --region=$REGION --service-account=$SA \
    --args=--source=crossref-batch,--batch-source=all,--limit=100,--apply \
    --set-env-vars=ELASTIC_ENDPOINT=$ELASTIC_ENDPOINT --set-secrets=ELASTIC_API_KEY=es-api-key:latest \
    --task-timeout=1800 --max-retries=0 --quiet | tail -1
  i=0
  for minute in 0 10 30; do
    job=${PULLERS[$i]}; sched=${SCHEDULES[$i]}; i=$((i + 1))
    gcloud scheduler jobs describe $sched --location=$REGION >/dev/null 2>&1 || \
    gcloud scheduler jobs create http $sched --location=$REGION --schedule="$minute * * * *" \
      --uri="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/$job:run" \
      --http-method=POST --oauth-service-account-email=$SA --quiet >/dev/null
  done
  schedules pause

  # --- dispatcher (Cloud Run service): two passes, because /run's OIDC audience is its own URL
  gcloud builds submit . --config=apps/dispatcher/cloudbuild.yaml --quiet | tail -1
  # "|"-separated (gcloud's ^|^ delimiter syntax): PAUSE_SCHEDULER_JOBS itself contains commas
  ENV="GCP_PROJECT=$PROJECT|GCP_REGION=$REGION|PUBSUB_PUSH_SA_EMAIL=$SA|CLAIMDRIFT_ES=$ELASTIC_ENDPOINT|CLAIMDRIFT_ELSER=.elser-2-elastic"
  ENV="$ENV|CLAIMDRIFT_MCP_URL=$(url claimdrift-mcp)/mcp|CLAIMDRIFT_ENGINES=/app/agents/engines.json|CLAIMDRIFT_DATA=/data|CLAIMDRIFT_DOCSTORE=/data/docstore"
  ENV="$ENV|PAUSE_SCHEDULER_JOBS=$(IFS=,; echo "${SCHEDULES[*]}")|KIBANA_URL=$KB|WORKFLOW_ID=dispatch-new-pairs"
  deploy_dispatcher() {
    gcloud run deploy claimdrift-dispatcher --image=$IMG/claimdrift-dispatcher:latest --region=$REGION --service-account=$SA \
      --set-env-vars="^|^${ENV}${1:+|PUBSUB_PUSH_AUDIENCE=$1}" \
      --set-secrets=WF_BEARER_TOKEN=wf-bearer-token:latest,CLAIMDRIFT_ES_API_KEY=es-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest \
      --add-volume=name=data,type=cloud-storage,bucket=$BUCKET --add-volume-mount=volume=data,mount-path=/data \
      --execution-environment=gen2 --allow-unauthenticated --timeout=600 --concurrency=4 --min-instances=0 --max-instances=3 \
      --memory=1Gi --cpu-boost --quiet | tail -1
  }
  deploy_dispatcher ""
  DISP=$(url claimdrift-dispatcher)
  deploy_dispatcher "$DISP/run"

  # --- Pub/Sub topic + push subscription -> /run
  gcloud pubsub topics describe claimdrift-dispatch >/dev/null 2>&1 || gcloud pubsub topics create claimdrift-dispatch --quiet
  # /dispatch publishes as the dispatcher's own service account
  gcloud pubsub topics add-iam-policy-binding claimdrift-dispatch --member=serviceAccount:$SA --role=roles/pubsub.publisher --quiet >/dev/null
  gcloud pubsub subscriptions describe claimdrift-dispatch-push >/dev/null 2>&1 || \
    gcloud pubsub subscriptions create claimdrift-dispatch-push --topic=claimdrift-dispatch --ack-deadline=600 \
      --push-endpoint="$DISP/run" --push-auth-service-account=$SA --push-auth-token-audience="$DISP/run" --quiet
  gcloud pubsub subscriptions update claimdrift-dispatch-push --push-endpoint="$DISP/run" \
    --push-auth-service-account=$SA --push-auth-token-audience="$DISP/run" --quiet >/dev/null

  # --- watermark (once) + closed budget, then the workflow
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: ApiKey $ELASTIC_API_KEY" "$ELASTIC_ENDPOINT/dispatch_state/_doc/main_flow")
  [[ $code == 200 ]] || es -X PUT "$ELASTIC_ENDPOINT/dispatch_state/_doc/main_flow?refresh=wait_for" \
    -d "{\"flow_name\": \"main_flow\", \"last_seen_ingested_at\": \"1970-01-01T00:00:00Z\", \"last_updated_at\": \"$(date -u +%FT%TZ)\"}" >/dev/null
  set_budget 0
  TOKEN=$(gcloud secrets versions access latest --secret=wf-bearer-token)
  # uploaded disabled: the pipeline starts paused
  sed "s#<WF_BEARER_TOKEN>#$TOKEN#; s#<DISPATCHER_URL>#$DISP#; s#^enabled: true\$#enabled: false#" \
    elastic/agent_builder/workflows/dispatch_new_pairs.template.yaml | upload_workflow
  echo "deployed (paused). dispatcher: $DISP"
fi

if [[ $STEP == batch ]]; then
  N=${2:?usage: pipeline.sh batch N}
  # pairs deferred while the last batch's budget was used up are picked up again (analysed ones are skipped)
  uv run python claimdrift/scripts/pipeline_watermark.py --apply
  set_budget "$N"
  workflow_enabled true
  schedules resume
  for j in "${PULLERS[@]}"; do gcloud run jobs execute $j --region=$REGION --async --quiet >/dev/null && echo "started $j"; done
  echo "batch of $N pairs started: pullers run now (and hourly until the dispatcher has dispatched $N pairs, then it pauses them)"
fi

if [[ $STEP == pause ]]; then
  schedules pause
  workflow_enabled false
  set_budget 0
  echo "paused: puller schedules paused, workflow disabled, dispatch budget closed"
fi

if [[ $STEP == status || $STEP == batch || $STEP == pause ]]; then
  echo "--- budget";   es "$ELASTIC_ENDPOINT/dispatch_state/_doc/dispatch_budget" | jq -c '._source | {budget_max, budget_used, analyzed_pairs}'
  echo "--- watermark"; es "$ELASTIC_ENDPOINT/dispatch_state/_doc/main_flow" | jq -c '._source.last_seen_ingested_at'
  echo "--- workflow"; curl -s "${KH[@]}" "$KB/api/workflows?size=100" | jq -c '.results[] | select(.id=="dispatch-new-pairs") | {enabled, valid}'
  echo "--- schedules"; for s in "${SCHEDULES[@]}"; do echo "$s $(gcloud scheduler jobs describe $s --location=$REGION --format='value(state)' 2>/dev/null)"; done
  echo "--- pairs waiting in preprints"; es "$ELASTIC_ENDPOINT/preprints/_count" -d '{"query":{"bool":{"filter":[{"exists":{"field":"published_doi"}},{"term":{"is_final_preprint":true}}],"must_not":[{"term":{"version":"published"}}]}}}' | jq -c .count
  echo "--- pipeline events"; es "$ELASTIC_ENDPOINT/drift_events/_search" -d '{"size":10,"sort":[{"detected_at":"desc"}],"query":{"term":{"record_source":"pipeline"}},"_source":["preprint_doi","detected_at","citation_analysis.status","fulltext_severity.tier"]}' | \
    jq -c '.hits.hits[]._source'
fi
