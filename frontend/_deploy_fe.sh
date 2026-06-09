#!/usr/bin/env bash
# One-shot frontend redeploy to Cloud Run. Mirrors the prior deploy in this repo.
set -euo pipefail
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
cd /home/riku_miku/claim_drift/frontend

PROJ=tensile-topic-496519-i1

BFF_URL=$(gcloud run services describe claimdrift-bff \
  --region=us-central1 --project="$PROJ" --format='value(status.url)')
PG_URL=$(gcloud run services describe claimdrift-playground \
  --region=us-central1 --project="$PROJ" --format='value(status.url)')

echo "==> BFF_URL=$BFF_URL"
echo "==> PG_URL=$PG_URL"

echo "==> Building image (gcloud builds submit)..."
gcloud builds submit . --config=cloudbuild.yaml --project="$PROJ" \
  --substitutions=_BFF_URL="$BFF_URL",_PLAYGROUND_URL="$PG_URL" 2>&1 | tail -6

echo "==> Deploying to Cloud Run..."
gcloud run deploy claimdrift-frontend \
  --image=us-central1-docker.pkg.dev/$PROJ/cloud-run-source-deploy/claimdrift-frontend:latest \
  --region=us-central1 --project="$PROJ" \
  --allow-unauthenticated 2>&1 | grep -aE 'revision|serving 100|Service URL|ERROR'

echo "==> DONE"
