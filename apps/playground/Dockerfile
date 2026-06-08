# Build context is the REPO ROOT, not apps/playground/. The Playground server
# imports code from THREE sibling trees:
#   - agents/scripts/e2_accumulation_curve.py  (CASE_INPUT + probe inject/teardown)
#   - agents/_shared/elastic_retrieval.py       (DRIFT_PATTERNS_INDEX)
#   - ingestion/common/elastic.py               (ElasticsearchHttpClient, pure urllib)
# plus the COMPARE fixtures JSON that _load_cases() reads at run time. Building
# from apps/playground/ alone would not see any of these.
#
# `gcloud run deploy --source` can't point at a Dockerfile in a subdirectory,
# so deploys go through Cloud Build (see cloudbuild.yaml):
#   gcloud builds submit . --config=apps/playground/cloudbuild.yaml
#   gcloud run deploy claimdrift-playground --image=<artifact URL> …
FROM python:3.12-slim

WORKDIR /app

# Deps first (least-frequently-changed layer).
COPY apps/playground/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The server's sys.path adds repo-root and repo-root/agents, then imports
#   agents.scripts.e2_accumulation_curve
#   _shared.elastic_retrieval         (resolved via the /app/agents entry)
#   ingestion.common.elastic
# Recreate exactly that layout under /app. server.py computes _REPO = parents[2]
# and _AGENTS = _REPO/agents from its own path, so it must live at
# /app/apps/playground/server.py for those to resolve to /app and /app/agents.
COPY apps/playground/server.py        /app/apps/playground/server.py
COPY apps/playground/orchestration.py /app/apps/playground/orchestration.py
COPY agents/scripts/e2_accumulation_curve.py /app/agents/scripts/e2_accumulation_curve.py
COPY agents/_shared/                  /app/agents/_shared/
COPY ingestion/                       /app/ingestion/
# Fixtures: _load_cases() reads agents/evals/fixtures/compare_outcome_switch_cases.json
COPY agents/evals/fixtures/compare_outcome_switch_cases.json \
     /app/agents/evals/fixtures/compare_outcome_switch_cases.json

# Implicit-namespace-package safety: agents/ and agents/scripts/ have no
# __init__.py in-repo (they're imported via sys.path tricks locally). Add empty
# ones so `agents.scripts.e2_accumulation_curve` resolves deterministically in
# the container. (Same approach as apps/dispatcher/Dockerfile.)
RUN touch /app/apps/__init__.py /app/apps/playground/__init__.py \
          /app/agents/__init__.py /app/agents/scripts/__init__.py

# Cloud Run injects PORT (default 8080).
ENV PORT=8080
EXPOSE 8080

# server.py lives at apps/playground/server.py and exposes `app`. uvicorn import
# string is module path from /app (which is on sys.path as cwd).
CMD exec uvicorn apps.playground.server:app --host 0.0.0.0 --port ${PORT}
