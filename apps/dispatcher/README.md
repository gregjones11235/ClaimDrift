# claimdrift-dispatcher

Cloud Run service that the scheduled Elastic Workflow calls to drive the
supervisor agent + persist its outputs to Elasticsearch + send notification
emails via Gmail.

See [ONBOARDING.md](ONBOARDING.md) (中文) / [ONBOARDING.en.md](ONBOARDING.en.md)
for the full design + step-by-step build plan.

## Local development

```bash
cp .env.example .env  # then fill in real values

pip install -r requirements.txt
uvicorn main:app --reload --port 8080

# Smoke test (in another terminal)
curl -X POST http://localhost:8080/dispatch \
  -H "Authorization: Bearer $(grep WF_BEARER_TOKEN .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"preprint_doi": "10.1101/2024.01.15.123456", "published_doi": "10.1016/j.cell.2024.05.001"}'
# Expect: {"status": "accepted"} in <100ms
```

Set `USE_STUB_STREAM=1` in `.env` during Step 1-6 development to replay the
captured supervisor event stream in `_stub_stream.json` instead of hitting the
live reasoning engine.

## Deploy

See [ONBOARDING.md §4 Step 7](ONBOARDING.md#step-7--cloud-run-部署约-30-分钟).
