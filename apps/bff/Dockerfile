# ClaimDrift BFF (review queue, author self-check) -- also the image of the citation-dispatch Cloud Run Job
# (`python -m claimdrift citation-worker --once`). Build context is the REPO ROOT (see /.gcloudignore):
#   gcloud builds submit . --config=apps/bff/cloudbuild.yaml
#
# Layout under /app mirrors the repo: server.py resolves ROOT = parents[2] = /app and imports apps.bff.*,
# ingestion.common.* and claimdrift. .env is absent in the container (config comes from Cloud Run env vars and
# Secret Manager). agents/engines.json lists the Agent Engine resource names (CLAIMDRIFT_ENGINES points at it), so
# rebuild this image after deploying new engines (updates in place keep the names stable).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080

COPY apps/requirements-cloud.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY claimdrift/ /app/claimdrift/
COPY agents/engines.json /app/agents/engines.json
COPY ingestion/common/ /app/ingestion/common/
COPY apps/bff/ /app/apps/bff/
# stdlib bioRxiv/medRxiv version resolver used by the on-demand fetch and the DOI pre-check
COPY apps/dispatcher/versions.py /app/apps/dispatcher/versions.py
RUN touch /app/apps/__init__.py /app/apps/bff/__init__.py /app/apps/dispatcher/__init__.py \
          /app/ingestion/__init__.py /app/ingestion/common/__init__.py

EXPOSE 8080
CMD exec python apps/bff/server.py
