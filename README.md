# ClaimDrift

> When a preprint becomes a peer-reviewed paper, its claims can shift — sometimes subtly, sometimes substantially. ClaimDrift detects these drifts and notifies the downstream researchers whose work depends on the original.

Submission for the [Google Cloud Rapid Agent Hackathon](https://rapid-agent.devpost.com/), Elastic Track.

> 🚧 **Work in progress.** This README will be expanded with architecture, demo video, and run instructions before submission. For now, see the per-subdirectory READMEs.

## Repository layout

| Directory | Owner | Contents |
|---|---|---|
| [`agents/`](./agents) | C | 5 Gemini agents built on Google ADK |
| [`ingestion/`](./ingestion) | B | Python pullers for arXiv / bioRxiv / medRxiv / Crossref / OpenAlex |
| [`apps/bff/`](./apps/bff) | B | Mock BFF and SSE endpoints for frontend development |
| [`elastic/`](./elastic) | B | Elasticsearch mappings, ELSER ingest pipeline, and demo seed data |
| [`contracts/`](./contracts) | B/D | Shared TypeScript contract types |
| [`prompts/`](./prompts) | A | Versioned prompts for the 5 agents |
| [`frontend/`](./frontend) | D | Next.js + Tailwind dashboard |
| [`workflows/`](./workflows) | C | Elastic Workflows orchestration |
| [`docs/`](./docs) | All | Cross-component contracts (`contracts.md`, `contracts_CN.md`) |

## Tech Stack

- **Agents**: Google Agent Development Kit (ADK) + Gemini, deployed on Cloud Run
- **Search & memory**: Elasticsearch (ELSER semantic search) + Elastic MCP server
- **Orchestration**: Elastic Workflows
- **Frontend**: Next.js 15, Tailwind, shadcn/ui, deployed on Vercel
- **Data sources**: arXiv, bioRxiv, medRxiv, Crossref, OpenAlex

## License

Apache License 2.0 — see [LICENSE](./LICENSE).
