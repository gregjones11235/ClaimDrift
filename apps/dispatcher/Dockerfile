# ClaimDrift dispatcher (automatic data pipeline, 新系统云端部署.md §4.6). Build context is the REPO ROOT:
#   gcloud builds submit . --config=apps/dispatcher/cloudbuild.yaml
# /run hands each pair to claimdrift.pipeline -> the supervisor on Vertex AI Agent Engine (agents/engines.json);
# the fetched v1 + published JATS go into the docstore on the bucket mounted at /data, where the MCP service reads
# them for quote verification.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080

COPY apps/requirements-cloud.txt /app/requirements-cloud.txt
COPY apps/dispatcher/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY claimdrift/ /app/claimdrift/
COPY agents/engines.json /app/agents/engines.json
COPY apps/dispatcher/main.py /app/apps/dispatcher/main.py
COPY apps/dispatcher/versions.py /app/apps/dispatcher/versions.py
RUN touch /app/apps/__init__.py /app/apps/dispatcher/__init__.py

EXPOSE 8080
CMD exec uvicorn apps.dispatcher.main:app --host 0.0.0.0 --port ${PORT}
