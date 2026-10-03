#!/usr/bin/env bash
# ClaimDrift Cloud Run deployment (新系统云端部署.md §4.3 / §4.5). Run in WSL from anywhere:
#   bash deploy/cloudrun.sh mcp | build | bff | playground | job | frontend | all
# Prerequisites: gcloud login + project, .env with ELASTIC_ENDPOINT / ELASTIC_API_KEY, the GCP base resources of
# §4.2 (Artifact Registry repo, bucket, secrets, service account), and -- for build/bff/playground/job -- the engines in
# agents/engines.json (claimdrift/scripts/deploy_agents.py): the images copy that file.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . .env; set +a
PROJECT=gen-lang-client-0220563082; REGION=us-central1; REPO=claimdrift
BUCKET=${PROJECT}-claimdrift-data; SA=claimdrift-run@$PROJECT.iam.gserviceaccount.com
AE_SA=service-$(gcloud projects describe $PROJECT --format='value(projectNumber)')@gcp-sa-aiplatform-re.iam.gserviceaccount.com
IMG=$REGION-docker.pkg.dev/$PROJECT/$REPO
STEP=${1:-all}
url() { gcloud run services describe "$1" --region=$REGION --format='value(status.url)' 2>/dev/null; }

# gcloud accepts ONE --set-env-vars and ONE --set-secrets per command (they cannot be combined with --update-*)
VOL="--add-volume=name=data,type=cloud-storage,bucket=$BUCKET --add-volume-mount=volume=data,mount-path=/data"
VOL_RO="--add-volume=name=data,type=cloud-storage,bucket=$BUCKET,readonly=true --add-volume-mount=volume=data,mount-path=/data"
BASE="--region=$REGION --service-account=$SA --quiet"

if [[ $STEP == mcp || $STEP == all ]]; then
  # private (ID token), stateless; the only writer of the Europe PMC cache; needs no ES and no Gemini key
  gcloud builds submit . --config=apps/mcp/cloudbuild.yaml --quiet | tail -1
  gcloud run deploy claimdrift-mcp --image=$IMG/claimdrift-mcp:latest $BASE $VOL --execution-environment=gen2 \
    --no-allow-unauthenticated --timeout=3600 --memory=2Gi --cpu=1 --max-instances=5 \
    --set-env-vars=CLAIMDRIFT_DATA=/data,CLAIMDRIFT_DOCSTORE=/data/docstore,CLAIMDRIFT_EPMC_CACHE=/data/cases/experiments/_cache_epmc
  for m in serviceAccount:$SA serviceAccount:$AE_SA; do
    gcloud run services add-iam-policy-binding claimdrift-mcp --region=$REGION --member=$m --role=roles/run.invoker --quiet >/dev/null
  done
fi

MCP_URL=$(url claimdrift-mcp)
ENV="CLAIMDRIFT_ES=$ELASTIC_ENDPOINT,CLAIMDRIFT_ELSER=.elser-2-elastic,CLAIMDRIFT_MCP_URL=$MCP_URL/mcp,CLAIMDRIFT_ENGINES=/app/agents/engines.json"
ENV_DATA="$ENV,CLAIMDRIFT_DATA=/data,CLAIMDRIFT_DOCSTORE=/data/docstore"
SECRETS="GEMINI_API_KEY=gemini-api-key:latest,CLAIMDRIFT_ES_API_KEY=es-api-key:latest"
SECRETS_GMAIL="$SECRETS,/secrets/gmail/token.json=gmail-token:latest"

if [[ $STEP == build || $STEP == all ]]; then
  gcloud builds submit . --config=apps/bff/cloudbuild.yaml --quiet | tail -1
  gcloud builds submit . --config=apps/playground/cloudbuild.yaml --quiet | tail -1
fi

if [[ $STEP == bff || $STEP == all ]]; then
  # read-write mount: the on-demand analysis fetches v1 + published JATS into /data/docstore (the MCP service reads them
  # for verify_quote); --no-cpu-throttling: that analysis runs in a background thread after the POST has returned
  gcloud run deploy claimdrift-bff --image=$IMG/claimdrift-bff:latest $BASE $VOL --execution-environment=gen2 \
    --set-env-vars=$ENV_DATA --set-secrets=$SECRETS \
    --allow-unauthenticated --timeout=300 --memory=1Gi --no-cpu-throttling --max-instances=2
fi

if [[ $STEP == playground || $STEP == all ]]; then
  # one run at a time (in-process lock) -> max 1 instance; the run is the supervisor on Agent Engine
  FE=$(url claimdrift-frontend || true)
  gcloud run deploy claimdrift-playground --image=$IMG/claimdrift-playground:latest $BASE $VOL_RO --execution-environment=gen2 \
    --set-env-vars="^@^${ENV_DATA//,/@}@GMAIL_TOKEN_FILE=/secrets/gmail/token.json${FE:+@PLAYGROUND_ALLOWED_ORIGINS=$FE}" \
    --set-secrets=$SECRETS_GMAIL --allow-unauthenticated --timeout=3600 --max-instances=1 --cpu-boost --memory=1Gi
fi

if [[ $STEP == job || $STEP == all ]]; then
  # citation queue: claims citation_runs, steps the citation_finder engine, writes verdicts, notifies the test inbox only
  gcloud run jobs deploy claimdrift-citation-dispatch --image=$IMG/claimdrift-bff:latest $BASE \
    --command=python --args=-m,claimdrift,citation-worker,--once \
    --set-env-vars=$ENV,GMAIL_TOKEN_FILE=/secrets/gmail/token.json --set-secrets=$SECRETS_GMAIL \
    --task-timeout=10800 --max-retries=1 --memory=1Gi
  gcloud scheduler jobs describe claimdrift-citation-tick --location=$REGION >/dev/null 2>&1 || \
  gcloud scheduler jobs create http claimdrift-citation-tick --location=$REGION --schedule="*/15 * * * *" \
    --uri="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/claimdrift-citation-dispatch:run" \
    --http-method=POST --oauth-service-account-email=$SA --quiet
fi

if [[ $STEP == frontend || $STEP == all ]]; then
  # NEXT_PUBLIC_* are inlined at build time: rebuild when the BFF / Playground URLs change
  gcloud builds submit frontend --config=frontend/cloudbuild.yaml --quiet \
    --substitutions=_BFF_URL=$(url claimdrift-bff),_PLAYGROUND_URL=$(url claimdrift-playground) | tail -1
  gcloud run deploy claimdrift-frontend --image=$IMG/claimdrift-frontend:latest --region=$REGION --allow-unauthenticated --quiet
  gcloud run services update claimdrift-playground --region=$REGION --quiet \
    --update-env-vars=PLAYGROUND_ALLOWED_ORIGINS=$(url claimdrift-frontend)
fi

for s in claimdrift-mcp claimdrift-bff claimdrift-playground claimdrift-frontend; do echo "$s $(url $s)"; done
