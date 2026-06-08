## 💡 Inspiration

During the pandemic, we watched preprints get cited and acted on within days. The problem is what happens next: peer review often moves the conclusion. An effect size shrinks, a hedge appears, a pre-registered primary outcome quietly becomes a secondary one. But the preprint keeps circulating with its original, stronger claim. Every paper that already cited it keeps building on a claim that has since drifted. And this is not a problem the pandemic left behind, it has only grown. In medicine, **33–45%** of preprints that get cited turn out to disagree with their own published version on the data or the conclusion. About **1–1.5 million** citations a year are resting on a claim that has since moved.

We looked at what it would take to actually solve this, and the fix is really a single chain: match a preprint to its published version, read both to see exactly what changed, find who cited the old one, and reach those downstream authors. In existing tools, these steps only exist in a scattered, partial way. ClaimDrift is built to **close this loop end to end** in an automatic, self-evolving way.

## 🔍 What it does

**ClaimDrift is a self-evolving multi-agent system that catches when a preprint's claims drift after peer review, traces the downstream papers that cited the old claim, and notifies their authors — automatically, end to end.**

Given a preprint and its published version, ClaimDrift runs a pipeline as follows:

- 🧩 **Extract** — decomposes each version into structured, sentence-level claims.
- 🔬 **Diff** — compares the final preprint against the published version and identifies exactly what moved (e.g., shrunken effect sizes, added hedging, disappeared claims, demoted primary outcome).
- 🧠 **Calibrate (the memory loop ⭐)** — the analyzer retrieves similar drifts from past cases via ELSER semantic search, and calibrates its severity score against their accumulated base rate. **The more drifts it has seen, the sharper its judgment gets.**
- 🔗 **Find** — uses OpenAlex citation edges to find the downstream papers that cited the drifted preprint, and tiers each one as `central`, `comparative`, or `peripheral` depending on how load-bearing the drifted claim was.
- ✉️ **Notify** — drafts and sends a neutral, informational email to each affected author. It quotes the original claim, the published version, and the reason it may matter.

The whole pipeline runs **self-driving on real data** — ~10k real preprints and ~2.2k real `(preprint, published)` pairs ingested from bioRxiv, medRxiv, and Crossref, with a live frontend that plays back each agent's reasoning in real time.

## 🛠️ How we built it

ClaimDrift is a **multi-agent system built end to end on Google Cloud and Elastic**. It spans **five Gemini reasoning agents** plus a layer of non-LLM orchestration, governance, and ingestion agents.

### 🤖 Reasoning agents

A pipeline of **five Gemini agents** on **Vertex AI Agent Engine**, each independently testable with its own input/output schema:

- **Claim Extractor** (`gemini-2.5-flash`) — decomposes each preprint and its published version into structured, sentence-level claims.
- **Drift Analyzer** (`gemini-2.5-pro`) — diffs the two claim sets and scores severity. This is the agent that reads the memory loop.
- **Citation Finder** (`gemini-2.5-flash`) — retrieve real **OpenAlex** citation edges for the drifted preprint via the Elastic MCP server, then matches the downstream papers and tiers each by how load-bearing the drifted claim was.
- **Notifier** (`gemini-2.5-flash`) — drafts a neutral, per-citation email and sends it via **Gmail API**.
- **Memory Synthesizer** (`gemini-2.5-pro`) — distills each drift into a reusable pattern and writes it back to Elastic.

### 🛰️ Orchestration, governance and ingestion agents

- **supervisor**: An **ADK** agent runs the five reasoning agents in a fixed order.
- **dispatcher**: A **Cloud Run** service that drives the supervisor, mints stable IDs, persists every agent's output to **Elasticsearch**, and sends the Notifier's emails through the **Gmail API**.
- **Curator**: an offline **Cloud Run Job** triggered every 24 hours to propose suggestions for keeping the memory bank clean. Suggestions cover merging duplicate patterns, refreshing stale descriptions and evicting low-quality rows.
- **Ingestion pullers**: Four **Cloud Run Jobs** pull the live corpus into Elasticsearch from bioRxiv, medRxiv, and Crossref, plus an on-demand OpenAlex puller fetches citation edges when the Citation Finder asks for them.

### 🧠 Memory loop

The memory loop distills patterns from every drift and stores them in **Elasticsearch**. When a new case comes in, the Drift Analyzer searches for similar past patterns via Elastic's semantic search model **ELSER** to calibrate how serious the new drift is.

## 🧗 Challenges we ran into

1. **Orchestrating agents into one coherent flow.** Getting separate agents to act as a single system meant the order, the hand-offs, and the shared identifiers all had to line up exactly. One agent's output is the next one's input, and a single missing field cascades into broken records downstream.

2. **Coordinating concurrent work.** Several runs can be in flight across instances at the same time, and the same paper can be picked up again before its first run finishes. We needed to manage this concurrency to avoid duplicate processing.

3. **Agents can confidently invent data.** Agents would happily produce perfectly plausible and entirely fake data, such as paper identifiers. We solved this by tool grounding and prompt engineering.

4. **Memory cleanup without hurting system performance.** The same phenomenon can quietly split into two near-duplicate records, weakening a signal that should have been counted once. However, deduplication in the main workflow would slow down the whole system. We designed an offline curator to cleanup system memory without hurting system performance.

5. **Designing an aesthetic & intuitive frontend** A frontend that is both genuinely intuitive and visually polished took several iterations of design.

## 🏆 Accomplishments that we're proud of

🔬 **A genuinely closed loop**
- An end-to-end pipeline — match → diff → find → notify — running fully automatically across 5 Gemini agents.
- Built and validated on **real data**: ~10k preprints and ~2.2k real `(preprint, published)` pairs.
- Real OpenAlex citation lookups and real Gmail notification dispatch.

🧠 **A memory loop that measurably works**
- The severity calibration is proven by a reproducible blind A/B test, which moved the verdict from 0.50 to 0.82 as prior examples grew.
- A two-tier governance design (fast append-only Synthesizer + conservative offline Curator) that keeps the base rate clean without slowing the write path.

⚙️ **Infrastructure that drives itself**
- A self-running trigger chain — Elastic Scheduled Workflow → Pub/Sub → Cloud Run — that detects new published pairs every 5 minutes and runs the pipeline in an unattended manner.

🎨 **A polished, intuitive interface**
- A genuinely intuitive, visually refined frontend with a considered, artful design rather than a bare functional dashboard.
- Plays back each agent's reasoning in real time, turning a complex multi-agent pipeline into something a researcher can follow at a glance.

🚀 **Production-ready, end to end**
- Fully deployed and self-driving on Google Cloud + Elastic. Five reasoning agents on Agent Engine, a Cloud Run dispatcher, and a live frontend, all running on real data.

## 📚 What we learned

**☁️ Cloud Run + Vertex AI carried the heavy lifting.** Deploying a long-running, multi-service pipeline could have been a nightmare. Cloud Run's serverless containers and Vertex AI Agent Engine let us ship each piece independently and scale it without managing infrastructure manually.

**🧩 ADK's convenience for agent orchestration.** Orchestrating five separate agents sounds daunting. ADK's structured approach to defining and sequencing agents made wiring them into a single, independently-testable pipeline really convenient.

**🔎 Elastic's remarkable built-in capability.** We were struck by how much works out-of-the-box in Elastic. ELSER delivers production grade semantic search, saving time from fine-tuning and building embedding pipeline manually. The Elastic MCP server exposes the index to our agents as ready-made tools they can call directly, which saves time from writing a custom bridge between the LLMs and the database.

**🔓 Multi-agent system unlocks real-world possibilities.** We solved a real-world problem by breaking the task into specialized agents and assembling them into an autonomous pipeline. We realized that this is a pattern with high adaptivity to almost any real-world challenges.

## 🚀 What's next for ClaimDrift

- **📎 Reference-manager integration.** Push drift alerts straight into where researchers actually work. The system will tag affected papers in a user's Zotero / Mendeley library, so the warning lands right next to the citation itself.
- **🔭 More drift types, more fields.** Detect a broader range of drift types, and extend beyond clinical trials into other disciplines where claims quietly move.
- **🌐 Real-world delivery.** Graduate notifications from team test inboxes to real author delivery with proper opt-out handling.

**Built with passion & love by Team ClaimDrift** — Jiayu Zhu (Alec) · System Architect & Agent Engineer — Jeremy · Data & Search Engineer — Ranjan · Frontend & Design Wizard

Our vision is to build a new infrastructure for science research by exploiting the unlimited potentials in multi-agent technology. Let's make science research more accurate, more convenient!