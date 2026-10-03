# ClaimDrift MCP tool service (部署方案 §4.3): `python -m claimdrift.mcp_server --http`, stateless streamable HTTP at /mcp.
# Build context is the repo root (see /.gcloudignore):
#   gcloud builds submit . --config=apps/mcp/cloudbuild.yaml
# Full texts, docstore and the Europe PMC cache come from the Cloud Storage bucket mounted at /data
# (CLAIMDRIFT_DATA=/data); this is the only service that writes the cache.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080
RUN pip install --no-cache-dir mcp==1.27.1

COPY claimdrift/ /app/claimdrift/

EXPOSE 8080
CMD exec python -m claimdrift.mcp_server --http --port ${PORT}
