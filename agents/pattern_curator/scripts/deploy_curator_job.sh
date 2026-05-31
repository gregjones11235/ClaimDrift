#!/usr/bin/env bash
#
# Create/update the pattern_curator Cloud Run JOB + a daily Cloud Scheduler
# trigger (C3 / D3). Run AFTER building the image:
#
#   # from repo root, in WSL bash (the proven path — same as the dispatcher):
#   gcloud builds submit . --config=agents/pattern_curator/cloudbuild.yaml
#   bash agents/pattern_curator/scripts/deploy_curator_job.sh
#
# Mirrors the dispatcher/puller deployment (docs/ingestion_cloud_run_ops.md,
# apps/dispatcher/README.md): same project, region, the cloud-run-source-deploy
# Artifact Registry repo, the elastic-api-key secret, and the
# claimdrift-scheduler service account.
#
# WHY a Cloud Run JOB (not a service, not an Agent Engine reasoning engine): the
# curator is batch code that runs to completion and exits. Its one Gemini call
# goes through google.genai on the Vertex backend.
#
# IAM NOTE (same gotcha the dispatcher hit, contracts.md changelog 2026-05-25):
# the Cloud Run runtime service account needs roles/aiplatform.user (for the
# Vertex Gemini call) and roles/secretmanager.secretAccessor (for
# elastic-api-key). Grant them to the job's SA if a run fails with a 403 on
# generate_content or the secret mount.
#
# OPERATING MODEL (safe-by-default, adopted after the 2026-05-31 incident): the
# scheduled daily run is DRY-RUN (the image CMD has no --apply). It only PROPOSES
# merges/evictions and logs them. A human reviews the proposals, then runs once
# with --apply to actually govern. The 2026-05-31 incident — an unbounded --apply
# image plus repeated manual runs merging 21 pairs across 4 executions (one timed
# out) — did NOT corrupt data (source_event_ids conservation verified, §3.5.1
# invariant held), but it was not controllable. Dry-run-by-default + human review
# makes governance auditable before any write.
#
# WHY daily + WHY the hour does not matter: the only contention the curator could
# cause is on the ELSER endpoint shared with live retrieval — and C1 already
# isolated that via the dedicated claimdrift-elser-batch endpoint. drift_patterns
# write contention is near-zero and guarded by optimistic concurrency. So
# "off-peak" = "low frequency + incremental scan" (already in the code), NOT "run
# at 3am". Daily, arbitrary hour.
set -euo pipefail

# Default config values come from agents/.env (the same file the local curator
# runs use) if not already exported. Load it so ELASTIC_ENDPOINT etc. are set
# without the operator having to export them by hand.
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_AGENTS_ENV="${_THIS_DIR}/../../.env"
if [[ -f "${_AGENTS_ENV}" ]]; then
  set -a; . "${_AGENTS_ENV}"; set +a
fi

PROJECT="${PROJECT:-${GOOGLE_CLOUD_PROJECT:-tensile-topic-496519-i1}}"
REGION="${REGION:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
IMAGE="${IMAGE:-us-central1-docker.pkg.dev/${PROJECT}/cloud-run-source-deploy/claimdrift-pattern-curator:latest}"
JOB="${JOB:-claimdrift-pattern-curator}"
SCHED="${SCHED:-claimdrift-pattern-curator-daily}"
CRON="${CRON:-0 4 * * *}"            # arbitrary daily hour — see header
TZ_NAME="${TZ_NAME:-America/New_York}"
SCHED_SA="${SCHED_SA:-claimdrift-scheduler@${PROJECT}.iam.gserviceaccount.com}"
ELASTIC_ENDPOINT="${ELASTIC_ENDPOINT:?ELASTIC_ENDPOINT not set and not found in agents/.env}"

echo ">>> Cloud Run Job ${JOB} (image ${IMAGE})"
# google.genai resolves the Vertex backend from the three GOOGLE_* env vars.
# Elastic creds match the puller jobs (api key from Secret Manager).
ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},GOOGLE_GENAI_USE_VERTEXAI=TRUE,ELASTIC_ENDPOINT=${ELASTIC_ENDPOINT}"

if gcloud run jobs describe "${JOB}" --project "${PROJECT}" --region "${REGION}" >/dev/null 2>&1; then
  echo "    (exists -> update)"
  gcloud run jobs update "${JOB}" \
    --project "${PROJECT}" --region "${REGION}" \
    --image "${IMAGE}" \
    --set-env-vars "${ENV_VARS}" \
    --set-secrets "ELASTIC_API_KEY=elastic-api-key:latest" \
    --max-retries 1 --task-timeout 1800s
else
  echo "    (new -> create)"
  gcloud run jobs create "${JOB}" \
    --project "${PROJECT}" --region "${REGION}" \
    --image "${IMAGE}" \
    --set-env-vars "${ENV_VARS}" \
    --set-secrets "ELASTIC_API_KEY=elastic-api-key:latest" \
    --max-retries 1 --task-timeout 1800s
fi

echo ">>> Cloud Scheduler ${SCHED} (${CRON} ${TZ_NAME}) -> run job"
JOB_RUN_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB}:run"
if gcloud scheduler jobs describe "${SCHED}" --project "${PROJECT}" --location "${REGION}" >/dev/null 2>&1; then
  echo "    (exists -> update)"
  gcloud scheduler jobs update http "${SCHED}" \
    --project "${PROJECT}" --location "${REGION}" \
    --schedule "${CRON}" --time-zone "${TZ_NAME}" \
    --uri "${JOB_RUN_URI}" --http-method POST \
    --oauth-service-account-email "${SCHED_SA}"
else
  echo "    (new -> create)"
  gcloud scheduler jobs create http "${SCHED}" \
    --project "${PROJECT}" --location "${REGION}" \
    --schedule "${CRON}" --time-zone "${TZ_NAME}" \
    --uri "${JOB_RUN_URI}" --http-method POST \
    --oauth-service-account-email "${SCHED_SA}"
fi

echo
echo ">>> Done. The scheduled run is DRY-RUN by default (proposes, writes nothing)."
echo ">>> Dry-run now (what the daily schedule does — review the proposed merges in logs):"
echo "    gcloud run jobs execute ${JOB} --project ${PROJECT} --region ${REGION}"
echo ">>> Apply (after reviewing proposals — actually merges/evicts):"
echo "    gcloud run jobs execute ${JOB} --project ${PROJECT} --region ${REGION} --args='--apply,--max-judgments=50'"
echo
echo ">>> NOTE: the Scheduler is created ENABLED. If you want it paused until you've"
echo "    reviewed a few dry-runs, run:"
echo "    gcloud scheduler jobs pause ${SCHED} --project ${PROJECT} --location ${REGION}"
