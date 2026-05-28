# Build context is the REPO ROOT, not apps/dispatcher/. This is because the
# dispatcher imports apps/bff/sse_adapter.py (the §6.1 SSE envelope translator
# that's shared with the BFF — single owner of the envelope shape). Building
# from apps/dispatcher/ alone would not see that file.
#
# `gcloud run deploy --source` does not support pointing at a Dockerfile in a
# subdirectory, so deploys go through Cloud Build:
#   gcloud builds submit . --config=apps/dispatcher/cloudbuild.yaml
#   gcloud run deploy claimdrift-dispatcher --image=<the artifact URL> …
FROM python:3.12-slim

WORKDIR /app

# Install dispatcher's deps first (the layer that changes least often).
COPY apps/dispatcher/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code: flat layout under /app, matching the "apps.bff.*" import
# path used by main.py so the shared translator resolves at runtime.
COPY apps/dispatcher/main.py /app/main.py
COPY apps/dispatcher/_stub_stream.json /app/_stub_stream.json
COPY apps/bff/sse_adapter.py /app/apps/bff/sse_adapter.py
RUN touch /app/apps/__init__.py /app/apps/bff/__init__.py

# Cloud Run injects PORT env var (default 8080)
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
