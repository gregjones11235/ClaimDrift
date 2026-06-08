#!/usr/bin/env bash
# Build + deploy the claimdrift-frontend Cloud Run service.
#
# NEXT_PUBLIC_BFF_URL is inlined into the client bundle at BUILD time (Next.js
# bakes NEXT_PUBLIC_* into the static JS), so it must be passed to Cloud Build —
# a runtime env var would never reach the browser. This script resolves the BFF
# URL from the already-deployed claimdrift-bff service so the two can't drift
# apart, then builds the image and rolls it out.
#
# Prereqs:
#   - gcloud authenticated against the project (gcloud auth login)
#   - run from the frontend/ directory
#
# Usage:
#   cd frontend
#   ./deploy.sh                      # auto-resolve BFF URL from claimdrift-bff
#   BFF_URL=https://my-bff ./deploy.sh   # override the BFF URL explicitly
set -euo pipefail

PROJECT_ID="tensile-topic-496519-i1"
REGION="us-central1"
IMAGE="us-central1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/claimdrift-frontend:latest"

# 1. Resolve the BFF URL (override via BFF_URL env var, else read from Cloud Run).
if [[ -z "${BFF_URL:-}" ]]; then
  echo "Resolving BFF URL from the deployed claimdrift-bff service..."
  BFF_URL="$(gcloud run services describe claimdrift-bff \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --format='value(status.url)')"
fi
if [[ -z "${BFF_URL}" || "${BFF_URL}" != https://* ]]; then
  echo "ERROR: could not resolve a valid BFF URL (got: '${BFF_URL:-}')." >&2
  echo "Pass it explicitly: BFF_URL=https://<bff-url> ./deploy.sh" >&2
  exit 1
fi
echo "Using NEXT_PUBLIC_BFF_URL=${BFF_URL}"

# 2. Build the image via Cloud Build, baking in the BFF URL at build time.
echo "Building image (this runs 'next build' with the BFF URL inlined)..."
gcloud builds submit . \
  --config=cloudbuild.yaml \
  --project="${PROJECT_ID}" \
  --substitutions=_BFF_URL="${BFF_URL}"

# 3. Roll the new image out to Cloud Run.
echo "Deploying claimdrift-frontend to Cloud Run..."
gcloud run deploy claimdrift-frontend \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --allow-unauthenticated

# 4. Print the live URL so you can verify immediately.
FRONTEND_URL="$(gcloud run services describe claimdrift-frontend \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format='value(status.url)')"
echo ""
echo "Done. Frontend live at: ${FRONTEND_URL}"
echo "Hard-refresh (Ctrl+Shift+R) to bypass the browser cache and see the new build."
