#!/usr/bin/env bash
set -e
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
PROJ=tensile-topic-496519-i1
cd ~/claim_drift
echo "=== building bff image ==="
gcloud builds submit . --config=apps/bff/cloudbuild.yaml --project=$PROJ 2>&1 | tail -4
echo "=== deploying bff (image only; env/secrets preserved) ==="
gcloud run deploy claimdrift-bff \
  --image=us-central1-docker.pkg.dev/$PROJ/cloud-run-source-deploy/claimdrift-bff:latest \
  --region=us-central1 --project=$PROJ \
  --allow-unauthenticated 2>&1 | grep -aE "revision|serving 100|Service URL|ERROR"
