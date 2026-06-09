# ClaimDrift — Demo Video Design Brief

**ClaimDrift**

Demo Video — Design Brief

*Because science shouldn't drift away in silence.*

**Impact**   **Tech**   **Design**   **Idea**

Google Cloud ADK Hackathon  ·  3:00 hard cap (target 2:50)

# 1. Video Concept

A three-act structure, ~2:50 total. The pacing deliberately lets each of the four judging criteria land on its own beat: **Problem (Impact) → Real Run (Tech) → Self-Improvement (Idea)**.

**Act One — The Problem & Value, Animated (0:00–0:35).** A short motion-graphics sequence dramatizes the pain point: a finding claimed by a preprint ("55 genome-wide significant genes, 92 pathogenic variants") quietly shrinks through peer review to "15 genes, 16 variants" — yet every paper citing it stays "lit up," none the wiser. The frame finally resolves to the logo and a one-line value promise.

**Transition (0:35–0:36).** A 1-second geometric transition with a "whoosh" sound effect cuts from flat animation into a real browser. **The background music begins here and runs continuously through the rest of the video.**

**Act Two — Live Demo, On Camera (0:36–2:50).** A live host or voiceover walks the audience through the product's real operation in real time — the architecture diagram, the agent pipeline lighting up step by step, the drift evidence, the auto-sent notifications, and the self-improvement climax. Everything shown is real, and it is all detected with zero human intervention.

**One-line caption:** *"Five Gemini agents running on Vertex AI, with Elastic as an MCP-based context + memory layer, forming a closed loop that learns from every drift it finds."*

# 2. Timeline

| **#** | **Time** | **Beat** | **Visuals / On-Screen** | **Criteria Hit** |
| --- | --- | --- | --- | --- |
| **S1** | 0:00–0:18 | Problem (animation) | A POTS whole-exome-sequencing preprint loudly claims "55 genome-wide significant genes, 92 pathogenic variants"; many papers cite it; after peer review the numbers quietly shrink to "15 genes, 16 variants," yet the citations stay lit and unaware. | Impact |
| **S2** | 0:18–0:35 | Value (animation → logo) | Citation links turn red one by one; logo + tagline; data sources slide in (bioRxiv · medRxiv · Crossref · OpenAlex). | Impact + Idea |
| **🔀** | 0:35–0:36 | Transition | 1-second geometric transition + whoosh SFX; flat animation cuts into a real browser. (Background music begins.) | — |
| **S3** | 0:36–0:58 | Overview + Architecture | Live voiceover / narration begins. Show the architecture diagram (data flowing once around the closed loop), then cut to the real dashboard — four real badges across the top: **Tracked events 389** (146 at ≥0.7 high severity), **Avg materiality_score 0.49** (across 389 events), **Affected citations 796** (742/747 emails sent), **Patterns learned 34**. Below is the real drift events list. (Exact numbers are whatever the frontend actually shows at record time.) | Design + Tech |
| **S4** | 0:58–1:24 | Pipeline run (live, real-time) | **/playground/orchestration** — hit RUN to run the full 5-agent orchestration live and in real time (Claim Extractor → Drift Analyzer → Citation Finder → Notifier → Memory Synthesizer). The horizontal pipeline **lights up node by node in real time** as real SSE events arrive (started/active/done); the final node writes back a distilled pattern and auto-sends the notification email. All real, zero human intervention, and judges can trigger it themselves. | Tech |
| **S5** | 1:24–1:44 | Drift evidence | /event/[id] shows the claim diff highlighted in red + the real materiality_score; /citations shows the real OpenAlex affected papers sorted by severity. (Exact numbers are whatever the frontend actually shows at record time — record a real fetched drift event, not demo_seed fake data.) | Tech + Impact |
| **S6** | 1:44–2:02 | Notifications | /notifications; auto-drafted and already-sent Gmail messages, quoting full sentences + links; one email per affected author. | Tech + Design |
| **S7** | 2:02–2:38 | Climax: Self-Improvement (A/B test) | Cut to the **/playground/memory-ab** interactive page and hit RUN to run the A/B test live: the same real, inherently ambiguous drift (NCT01163032 Tasimelteon clinical trial, where a pre-specified co-primary endpoint was downgraded to a subordinate "step-down" endpoint), run through the same production prompt across **three side-by-side state cards — No memory / support=5 / support=20**. With **no memory**, the analyzer can only say "this matters in itself" (materiality 0.75, rationale reads *intrinsic significance*); **after recalling memory**, it recognizes "this is a known, recurring manipulation playbook used to dress up success" (0.85), and the stronger the evidence the more confident the wording (support 5 *increase confidence* → support 20 *strong support*). **The number capping at 0.85 is exactly what proves the system has a sane ceiling and doesn't inflate the score for looks** — the value is in the reasoning upgrade in the rationale, not in number inflation. | Idea + Tech |
| **S8** | 2:38–3:00 | Closing | Quick recap of the architecture diagram; the golden memory-loop edge highlights last; logo + tagline + repo / hosted URL. | All |

**Note:** The cap is set at 2:50 to leave a buffer (only the first 3:00 is judged). **S4** runs the real 5-agent orchestration live at **/playground/orchestration** (hit RUN to trigger; nodes light up one by one with real SSE); for a stable, reproducible fallback that needs no live GCP credentials, the BFF still supports the **SSE_REPLAY_GOLDEN=1** "golden" event stream (via the /live AgentTimeline view) as a backup. **S7**'s A/B test is real data: all three memory conditions run through the same production `drift_analyzer` prompt against the real Elasticsearch, and the analyzer reads support_count out of the retrieved pattern (the case input never tells it the number), so the rationale shift can only come from the recalled historical base rate, not something hard-coded into the prompt — this is the strongest possible answer to "did you just write the conclusion into the prompt?" English subtitles are mandatory; royalty-free background music plays throughout Act Two.

# 3. System Architecture

First presented as a global diagram in S3, then brought back at the close (S8) with the golden memory edge highlighting last. The two weightiest points: the **Google Cloud × Elastic dual stack**, and the **self-improving memory loop** (the golden edge) — the Memory Synthesizer writes a distilled pattern that the Drift Analyzer recalls on its next run.

*Five Gemini agents running on Vertex AI; Elasticsearch as the context + memory layer accessed via MCP; a closed loop in which the Memory Synthesizer writes a distilled pattern that the Drift Analyzer recalls on its next run.*
