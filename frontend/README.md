# ClaimDrift — Frontend

> **Status (2026-05-29)**: Live. Next.js 16 + React 19 + Tailwind + shadcn/ui dashboard, wired to real data through the [BFF](../apps/bff/). Six views: drift-event list (`/`), drift-event detail (`/event/[id]`), affected citations, notifications, memory patterns (`/patterns`), and the live agent-activity stream (`/live`).

Per the [team allocation in `../docs/contracts.md`](../docs/contracts.md) §0, the dashboard is **D (tty / Ranjan)**-owned.

## How it talks to the backend

The frontend is a thin view layer over the BFF — it holds no Elastic credentials and never queries Elasticsearch directly.

- **REST**: server-side `fetch` in [`src/lib/api/client.ts`](src/lib/api/client.ts) calls the BFF's `/api/*` endpoints.
- **SSE**: the live view opens a browser `EventSource` against the BFF's `/api/events/stream` (see [`src/lib/store/sse.ts`](src/lib/store/sse.ts)).
- **Types**: response shapes are typed in [`src/types/claimdrift.ts`](src/types/claimdrift.ts), tracking [`../contracts/claimdrift_types.ts`](../contracts/claimdrift_types.ts) and the §6.1 SSE envelope in [`../docs/contracts.md`](../docs/contracts.md).

The BFF base URL is read from **`NEXT_PUBLIC_BFF_URL`** (falls back to `http://127.0.0.1:8787` for local dev). Because it is a `NEXT_PUBLIC_*` var, Next.js **inlines it into the client bundle at build time** — when deploying, it must be set *before* `next build`, not just at runtime.

## Run locally

The frontend needs the BFF running (see [`../apps/bff/README.md`](../apps/bff/README.md)).

```bash
# 1. In one terminal, start the BFF (serves real ES data):
uv run --project agents python apps/bff/server.py

# 2. In another terminal, start the frontend:
cd frontend
npm install
npm run dev          # http://localhost:3000, talks to http://127.0.0.1:8787 by default
```

To point the dev server at a non-default BFF, set `NEXT_PUBLIC_BFF_URL` in `frontend/.env.local`.

## Deploy (Cloud Run)

The whole hosted demo runs on Google Cloud — see the root [`README.md`](../README.md) "Deploy the hosted demo (frontend + BFF)" section for the two-service recipe. In short:

1. Deploy the BFF first (`apps/bff/`), note its public Cloud Run URL.
2. Build + deploy this frontend with `NEXT_PUBLIC_BFF_URL` baked in to that URL:
   ```bash
   cd frontend
   gcloud builds submit . --config=cloudbuild.yaml --substitutions=_BFF_URL=https://<bff-url>
   gcloud run deploy claimdrift-frontend \
     --image=us-central1-docker.pkg.dev/<project>/cloud-run-source-deploy/claimdrift-frontend:latest \
     --region=us-central1 --allow-unauthenticated
   ```

The resulting frontend Cloud Run URL is the **"URL to the hosted Project for judging and testing"** for the Devpost submission.
