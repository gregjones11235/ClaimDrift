# ClaimDrift — Frontend

> **Status (2026-05-26)**: Frontend scaffolding incoming from D. This directory is currently empty.

Per the [team allocation in `../docs/contracts.md`](../docs/contracts.md) §0, the dashboard is **D (tty / Ranjan)**-owned: Next.js + Tailwind + shadcn/ui, six views covering preprints, drift events, affected citations, memory loop visualization, and live agent activity.

When this directory has code:

- **Setup, run, and deploy instructions** will live in this README.
- **BFF endpoint contract** (the API the frontend talks to) is in [`../apps/bff/`](../apps/bff/) and authoritatively typed in [`../contracts/claimdrift_types.ts`](../contracts/claimdrift_types.ts).
- **SSE event envelope** the frontend's "agent activity" view consumes is specified in [`../docs/contracts.md`](../docs/contracts.md) §6.1. The production SSE adapter (translating Vertex AI Agent Engine `streamQuery` events into the §6.1 envelope shape) is tracked as TODO C — it does not yet exist; the current BFF serves a mock SSE stream so frontend development can proceed in parallel.

Until the frontend ships, evaluators can verify end-to-end system behavior via the dispatcher reference run + ES inspection — see the root [`README.md`](../README.md) "Reproduce from a clean clone" section.
