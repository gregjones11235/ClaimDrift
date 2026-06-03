# pattern_curator Cloud Run Job image (C3 / D3).
#
# The curator is a batch job, NOT an ADK LlmAgent / Agent Engine reasoning
# engine (design B.1). It runs to completion and exits — Cloud Scheduler
# triggers it once a day. It makes ONE Gemini call per candidate pair via
# google.genai on the Vertex backend (so it needs the GOOGLE_CLOUD_* env +
# project ADC, injected by the Cloud Run Job config), and talks to Elastic via
# ELASTIC_ENDPOINT / ELASTIC_API_KEY (the api key from Secret Manager, same as
# the puller jobs).
#
# Build context is the REPO ROOT (so both agents/ and ingestion/ are copyable):
#   gcloud builds submit --config agents/pattern_curator/cloudbuild.yaml .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Runtime deps. Pinned loosely; the curator only needs the Elastic HTTP client
# (stdlib urllib, no dep), google-genai for the one LLM call, jsonschema for the
# guardrail gate, and python-dotenv (harmless if .env is absent in the job).
RUN pip install --no-cache-dir \
    "google-genai>=1.75.0" \
    "jsonschema>=4.0" \
    "python-dotenv>=1.2.2"

# curator imports `pattern_curator.*`, `_shared.*`, and `ingestion.common.*`.
# Copy the packages it actually touches; keep the image lean (no other agents).
COPY agents/pattern_curator ./pattern_curator
COPY agents/_shared ./_shared
COPY ingestion ./ingestion

# Default to DRY-RUN (no --apply): a scheduled run only PROPOSES merges/evictions
# and logs them; it writes nothing. A human reviews the proposed merges in the
# logs, then runs once WITH --apply to actually govern. This is the safe-by-
# default operating model adopted after the 2026-05-31 incident where an
# unbounded --apply image plus repeated manual runs merged more aggressively than
# intended (data stayed sound — conservation verified — but the run was not
# controllable). To actually write, override args at execute time:
#   gcloud run jobs execute claimdrift-pattern-curator --args="--apply,--max-judgments=50"
ENTRYPOINT ["python3", "-m", "pattern_curator.curator"]
CMD ["--max-judgments=50"]
