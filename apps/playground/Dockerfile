# ClaimDrift Playground (live 5-agent run over SSE). Build context is the REPO ROOT (see /.gcloudignore):
#   gcloud builds submit . --config=apps/playground/cloudbuild.yaml
#
# The run itself is the supervisor agent on Vertex AI Agent Engine (CLAIMDRIFT_ENGINES -> agents/engines.json);
# this service loads the pair's full texts from the bucket mounted at /data, relays the node frames and sends the
# drafted alerts (Gmail token from Secret Manager). server.py computes _REPO = parents[2], so it must live at
# /app/apps/playground/server.py.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080

COPY apps/requirements-cloud.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY claimdrift/ /app/claimdrift/
COPY agents/engines.json /app/agents/engines.json
COPY apps/playground/server.py /app/apps/playground/server.py
COPY apps/playground/orchestration.py /app/apps/playground/orchestration.py
COPY apps/dispatcher/versions.py /app/apps/dispatcher/versions.py
RUN touch /app/apps/__init__.py /app/apps/playground/__init__.py /app/apps/dispatcher/__init__.py

EXPOSE 8080
CMD exec uvicorn apps.playground.server:app --host 0.0.0.0 --port ${PORT}
