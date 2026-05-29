# Build context is the REPO ROOT, not apps/bff/. server.py imports two
# repo-relative modules — ingestion/common/elastic.py (the stdlib-only ES HTTP
# client) and apps/bff/sse_adapter.py (the §6.1 SSE envelope translator). A
# build context scoped to apps/bff/ alone would not see ingestion/.
#
# `gcloud run deploy --source` cannot point at a Dockerfile outside the build
# context root, so deploys go through Cloud Build (mirrors the dispatcher):
#   gcloud builds submit . --config=apps/bff/cloudbuild.yaml
#   gcloud run deploy claimdrift-bff --image=<the artifact URL> …
FROM python:3.12-slim

WORKDIR /app

# Install BFF deps first (the layer that changes least often).
COPY apps/bff/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code. server.py resolves ROOT = parents[2], i.e. /app, and does
# sys.path.append(ROOT), so the "apps.bff.*" and "ingestion.common.*" import
# paths must exist under /app exactly as in the repo.
COPY apps/bff/server.py /app/apps/bff/server.py
COPY apps/bff/sse_adapter.py /app/apps/bff/sse_adapter.py
COPY ingestion/common /app/ingestion/common
# The golden replay file is optional but lets the live SSE view animate without
# GCP creds when SSE_REPLAY_GOLDEN=1 (see apps/bff/README.md).
COPY apps/dispatcher/tests/golden/stream_amblyopia_v2.jsonl /app/apps/dispatcher/tests/golden/stream_amblyopia_v2.jsonl
RUN touch /app/apps/__init__.py /app/apps/bff/__init__.py \
          /app/ingestion/__init__.py /app/ingestion/common/__init__.py

# Cloud Run injects $PORT (default 8080); server.py honors it (and binds
# 0.0.0.0 automatically when $PORT is set). agents/.env is absent in the
# container, so load_dotenv is a no-op and the real ELASTIC_* values come from
# Cloud Run env vars / secrets.
ENV PORT=8080
EXPOSE 8080

# server.py is a plain http.server; run it as a module from /app.
CMD exec python apps/bff/server.py
