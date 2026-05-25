FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY ingestion ./ingestion
COPY elastic ./elastic

ENTRYPOINT ["python3", "-m", "ingestion.run_pull"]
CMD ["--help"]
