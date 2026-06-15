"use client";

import { useEffect, useMemo, useState } from "react";
import {
  runOrchestration,
  NodeId,
  NodeView,
  NodeLane,
  NodePhase,
  EmailEvent,
  OrchestrationMeta,
} from "@/lib/playground";
import { useRunGuard } from "@/lib/useRunGuard";

// Initial pipeline, fixed order. Mirrors apps/playground/orchestration.py PIPELINE.
const INITIAL_NODES: NodeView[] = [
  { id: "claim_extractor", label: "Claim Extractor", fanout: true, lanes: { 0: { phase: "idle" } } },
  { id: "drift_analyzer", label: "Drift Analyzer", fanout: false, lanes: { 0: { phase: "idle" } } },
  { id: "citation_finder", label: "Citation Finder", fanout: false, lanes: { 0: { phase: "idle" } } },
  { id: "notifier", label: "Notifier", fanout: true, lanes: { 0: { phase: "idle" } } },
  { id: "memory_synthesizer", label: "Memory Synthesizer", fanout: false, lanes: { 0: { phase: "idle" } } },
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// The preprint DOI this playground's fixed case runs on — must match
// apps/playground/orchestration.py ENVELOPE["preprint"]["doi"].
const PREWARM_PREPRINT_DOI = "10.1101/2024.05.01.24306384";

// Silently pre-warm the OpenAlex `cites:` lookup so the FIRST real run's
// citation_finder isn't paying for a cold OpenAlex query cache. citation_finder
// reaches OpenAlex through the Elastic `openalex_citing_works` MCP workflow,
// which does a TWO-step chain: (1) GET /works/{doi} to map the DOI → short
// OpenAlex work id, (2) GET /works?filter=cites:{id}. We replicate BOTH calls
// here with the EXACT same URLs (including the `select=` query strings, which
// are part of OpenAlex's cache key) so this prewarm hits the same cache entries
// the workflow will. We can only warm OpenAlex's own server-side query cache —
// NOT the Elastic node's outbound connection pool (that's server-side) — but
// that cache is the dominant cold cost for a fixed, repeatedly-queried DOI.
//
// Best-effort: every error is swallowed. OpenAlex is a public, CORS-enabled,
// unauthenticated API and this fires zero LLM calls / sends zero email, so it
// costs nothing. If it fails (offline, OpenAlex down, CORS change), the real
// run just pays the cold cost as before — no user-visible effect either way.
async function prewarmOpenAlex(signal: AbortSignal): Promise<void> {
  try {
    const src = await fetch(
      `https://api.openalex.org/works/https://doi.org/${PREWARM_PREPRINT_DOI}?select=id,doi,display_name`,
      { signal, headers: { Accept: "application/json" } },
    );
    if (!src.ok) return;
    const srcJson = (await src.json()) as { id?: string };
    const workId = srcJson.id?.split("/").pop();
    if (!workId) return;
    await fetch(
      `https://api.openalex.org/works?filter=cites:${workId}` +
        `&per-page=25&select=id,doi,title,display_name,authorships,publication_year,cited_by_count`,
      { signal, headers: { Accept: "application/json" } },
    );
  } catch {
    // best-effort prewarm — ignore all failures (incl. AbortError on unmount)
  }
}

// A node has "started" once any of its lanes left idle — i.e. at least one
// real SSE event arrived for it. Used to drive the in-order "working" inference
// for the node immediately downstream (which is otherwise stuck at idle during
// its buffered work window).
function nodeStarted(node: NodeView): boolean {
  return Object.values(node.lanes).some((l) => l.phase !== "idle");
}

function phaseColor(phase: NodePhase): string {
  switch (phase) {
    case "active":
      return "var(--y)";
    case "done":
      return "var(--grn)";
    case "error":
      return "var(--rd)";
    default:
      return "var(--gr3)";
  }
}

export default function OrchestrationPage() {
  const [nodes, setNodes] = useState<NodeView[]>(INITIAL_NODES);
  const [meta, setMeta] = useState<OrchestrationMeta | null>(null);
  const [email, setEmail] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emails, setEmails] = useState<EmailEvent[]>([]);
  const [drift, setDrift] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [quotaError, setQuotaError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [abort, setAbort] = useState<(() => void) | null>(null);

  const emailValid = useMemo(() => EMAIL_RE.test(email.trim()), [email]);

  // The notifier denominator is the number of notifications we can ACTUALLY
  // generate (one lane per affected citation that had enough metadata to draft
  // an alert) — NOT citation_finder's `total_found`. OpenAlex can report N
  // citing works while only M (<N) carry the DOI/author a notifier needs, so a
  // total_found denominator strands the card at "M/N" forever (the missing
  // N−M lanes never light up). We instead count real lanes and treat the
  // fan-out as COMPLETE once the downstream memory_synthesizer node begins —
  // supervisor runs every notifier before memory_synthesizer, so its first
  // event means no more notifier lanes are coming. Until then the denominator
  // can still climb as lanes arrive (so we never under-count mid-fan-out).
  const notifierSealed = useMemo(() => {
    const ms = nodes.find((n) => n.id === "memory_synthesizer");
    return Object.values(ms?.lanes ?? {}).some((l) => l.phase !== "idle");
  }, [nodes]);

  // Pre-warm the OpenAlex citing-works cache as soon as the page mounts, while
  // the judge is still reading "THE CASE" and entering their email. By the time
  // they press Run, citation_finder's OpenAlex lookup is likely a cache hit
  // rather than a cold query. Fire once on mount; abort if we unmount first.
  useEffect(() => {
    const ctrl = new AbortController();
    void prewarmOpenAlex(ctrl.signal);
    return () => ctrl.abort();
  }, []);

  // The supervisor pipeline can run up to ~6 min; warn before any navigation
  // that would unmount this page and kill the live SSE run. See useRunGuard.
  useRunGuard(
    running,
    "The 5-agent pipeline is still running (~6 min). Leaving this page will stop it and you won't get the drift-alert email. Leave anyway?",
  );

  function patchLane(id: NodeId, lane: number, next: Partial<NodeLane>) {
    setNodes((cur) =>
      cur.map((n) =>
        n.id === id
          ? { ...n, lanes: { ...n.lanes, [lane]: { ...(n.lanes[lane] ?? { phase: "idle" }), ...next } } }
          : n,
      ),
    );
  }

  function start() {
    setNodes(INITIAL_NODES);
    setMeta(null);
    setError(null);
    setEmails([]);
    setDrift(null);
    setQuotaError(null);
    setRetrying(null);
    setStatus("starting…");
    setLog([]);
    setRunning(true);

    const stop = runOrchestration(
      email.trim(),
      (type, data) => {
        setLog((l) => [...l, `${type}  ${JSON.stringify(data).slice(0, 140)}`]);
        const node = data.node as NodeId | undefined;
        const lane = (data.lane as number | null) ?? 0;
        switch (type) {
          case "run.started":
            setMeta(data as unknown as OrchestrationMeta);
            break;
          case "pipeline.warming":
            setStatus((data.detail as string) ?? "starting supervisor engine…");
            break;
          case "pipeline.ready":
            setStatus(null);
            break;
          case "node.started":
            setStatus(null);
            if (node) patchLane(node, lane, { phase: "active" });
            break;
          case "node.active":
            if (node) patchLane(node, lane, { phase: "active", action: data.action as string });
            break;
          case "node.output":
            if (node) patchLane(node, lane, { summary: data.summary as string });
            break;
          case "node.done":
            if (node)
              patchLane(node, lane, { phase: "done", summary: data.summary as string, action: undefined });
            break;
          case "node.error":
            if (node) patchLane(node, lane, { phase: "error", error: data.message as string });
            break;
          case "pipeline.retrying":
            // A gemini-2.5-pro sub-agent (claim_extractor OR drift_analyzer)
            // returned empty (suspected/confirmed 429). The backend is retrying
            // the whole supervisor run after a backoff. Mark the ACTUAL culprit
            // node (from data.node) so the judge sees which layer 429'd, not a
            // dead board. See orchestration.py retry wrapper.
            patchLane(node ?? "drift_analyzer", 0, {
              phase: "error",
              error: data.confirmed ? "429 quota (confirmed) — retrying" : "429 quota (suspected) — retrying",
            });
            setRetrying((data.detail as string) ?? "Retrying after a quota backoff…");
            setStatus(`retrying (attempt ${(data.attempt as number) + 1}/${data.max_attempts})…`);
            break;
          case "pipeline.reset":
            // Backend is starting a fresh supervisor attempt — clear the board
            // so the judge watches it re-run from the top.
            setNodes(INITIAL_NODES);
            setDrift(null);
            setEmails([]);
            break;
          case "pipeline.quota_error":
            // A gemini-2.5-pro sub-agent (claim_extractor OR drift_analyzer) hit
            // a Vertex shared-quota 429 and returned empty, stalling the chain.
            // Mark the ACTUAL culprit node (from data.node) failed AND surface a
            // banner so the judge knows WHICH layer stopped it (not a silent
            // hang, not always drift_analyzer). See orchestration.py.
            patchLane(node ?? "drift_analyzer", 0, {
              phase: "error",
              error: data.confirmed ? "429 quota (confirmed)" : "429 quota (suspected)",
            });
            setQuotaError((data.detail as string) ?? null);
            break;
          case "drift.minted":
            // success on this (possibly retried) attempt — clear any retry banner
            setRetrying(null);
            setDrift((data.drift_summary as string) ?? null);
            break;
          case "email.sending":
            setEmails((e) => [...e, { to: data.to as string, subject: data.subject as string, status: "sending" }]);
            break;
          case "email.sent":
            setEmails((e) =>
              upsertEmail(e, data.subject as string, {
                to: data.to as string,
                subject: data.subject as string,
                status: "sent",
                message_id: data.message_id as string,
              }),
            );
            break;
          case "email.failed":
            setEmails((e) =>
              upsertEmail(e, data.subject as string, {
                to: data.to as string,
                subject: data.subject as string,
                status: "failed",
                error: data.error as string,
              }),
            );
            break;
          case "run.complete":
            setRunning(false);
            break;
        }
      },
      (msg) => {
        setError(msg);
        setRunning(false);
      },
      () => setRunning(false),
    );
    setAbort(() => stop);
  }

  function stop() {
    abort?.();
    setRunning(false);
  }

  return (
    <div style={{ maxWidth: 1180 }}>
      {/* Header */}
      <div style={{ marginBottom: 18 }}>
        <h1
          style={{
            fontFamily: "var(--display)",
            fontSize: 28,
            color: "var(--wh)",
            letterSpacing: "0.01em",
            // Syne 800 descenders (g/y/p) overshoot the em-box — reserve real
            // pixels below the baseline so they aren't clipped.
            display: "block",
            lineHeight: 1.4,
            paddingBottom: 8,
            marginBottom: 8,
            overflow: "visible",
          }}
        >
          5-Agent Orchestration Playground
        </h1>
        <p style={{ fontSize: 13, lineHeight: 1.7, color: "var(--gr)", maxWidth: 760 }}>
          One real preprint→published pair is run through the{" "}
          <span style={{ color: "var(--y)" }}>full 5-agent supervisor</span>{" "}live. Watch the
          pipeline light up node-by-node — two claim extractors in parallel, then drift analysis,
          citation discovery, the per-citation notifier fan-out, and the memory-loop write. Enter
          your email and you&rsquo;ll receive the actual drift-alert this pipeline sends.
        </p>

        {/* What the case is — mirrors the A/B playground's "THE CASE" box so the
            preprint/published DOIs aren't opaque and the judge sees this is a
            real paper with a real, verified drift. */}
        <div
          style={{
            marginTop: 14,
            padding: "12px 14px",
            border: "1px solid var(--gr3)",
            background: "var(--bk2)",
            maxWidth: 760,
            fontSize: 12,
            lineHeight: 1.7,
            color: "var(--gr)",
          }}
        >
          <div className="specimen specimen-y" style={{ marginBottom: 6 }}>
            the case
          </div>
          <strong style={{ color: "var(--wh)", fontWeight: 600 }}>
            &ldquo;Evidence that minocycline treatment confounds the interpretation
            of neurofilament as a biomarker&rdquo;
          </strong>{" "}
          (medRxiv 2024 → <em>Brain Communications</em>). Between the preprint and the
          published version, a battery of precise quantitative findings — a{" "}
          <em>3.5-fold plasma / 5.7-fold CSF NfL spike</em>, a <em>&gt;500&nbsp;pg/mL</em>{" "}
          threshold, <em>1.3–4.0-fold</em> increases in mice — were de-quantified into
          vague qualitative statements (&ldquo;NfL spiked&rdquo;, &ldquo;high NfL&rdquo;),
          and a negative-control claim (&ldquo;dermatology patients not elevated&rdquo;)
          was dropped entirely. The pipeline detects this drift and notifies the{" "}
          <span style={{ color: "var(--y)" }}>real papers that cite the preprint</span>.
          <div style={{ marginTop: 8, fontSize: 11 }}>
            <a
              href="https://doi.org/10.1101/2024.05.01.24306384"
              target="_blank"
              rel="noopener"
              style={{ color: "var(--bl)", textDecorationLine: "none" }}
            >
              10.1101/2024.05.01.24306384
            </a>{" "}
            <span style={{ opacity: 0.7 }}>— the medRxiv preprint (what was claimed)</span>
            <br />
            <a
              href="https://doi.org/10.1093/braincomms/fcaf175"
              target="_blank"
              rel="noopener"
              style={{ color: "var(--bl)", textDecorationLine: "none" }}
            >
              10.1093/braincomms/fcaf175
            </a>{" "}
            <span style={{ opacity: 0.7 }}>
              — the published Brain Communications paper (what was delivered)
            </span>
          </div>
        </div>
      </div>

      {/* Run control: email + button */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 22, flexWrap: "wrap" }}>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={running}
          placeholder="your@email.com — where to send the drift alert"
          style={{
            background: "var(--bk2)",
            border: `1px solid ${email && !emailValid ? "var(--rd)" : "var(--gr3)"}`,
            color: "var(--wh)",
            padding: "11px 14px",
            fontFamily: "var(--mono)",
            fontSize: 12,
            width: 340,
            outline: "none",
          }}
        />
        <button
          onClick={running ? stop : start}
          disabled={!running && !emailValid}
          style={{
            background: running ? "transparent" : emailValid ? "var(--y)" : "var(--gr3)",
            color: running ? "var(--rd)" : emailValid ? "var(--bk)" : "var(--gr)",
            border: running ? "1px solid var(--rd)" : `1px solid ${emailValid ? "var(--y)" : "var(--gr3)"}`,
            padding: "12px 26px",
            fontFamily: "var(--mono)",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            cursor: !running && !emailValid ? "not-allowed" : "pointer",
            transition: "all 0.2s",
          }}
        >
          {running ? "■ Stop run" : "▶ Run pipeline"}
        </button>
        {meta?.case && (
          <span className="specimen" style={{ color: "var(--gr)" }}>
            {meta.case.preprint_doi} → {meta.case.published_doi}
          </span>
        )}
        {error && (
          <span className="specimen" style={{ color: "var(--rd)" }}>
            ⚠ {error}
          </span>
        )}
      </div>

      {/* Warming banner — shown while the (possibly cold) supervisor engine spins
          up, so the board isn't a frozen blank during the import. */}
      {running && status && (
        <div
          className="specimen specimen-y"
          style={{
            marginBottom: 16,
            padding: "8px 12px",
            border: "1px solid var(--y)",
            background: "var(--bk2)",
            color: "var(--y)",
            display: "inline-block",
          }}
        >
          <span style={{ marginRight: 8 }}>●</span>
          {status} <span style={{ opacity: 0.6 }}>(first run on a cold instance can take ~15s)</span>
        </div>
      )}

      {/* retry-in-progress banner — drift_analyzer returned empty (429), the
          backend is re-running the whole supervisor after a backoff. Shown
          while quotaError is still null so the judge sees we're trying again. */}
      {retrying && !quotaError && (
        <div
          style={{
            marginBottom: 16,
            padding: "10px 14px",
            border: "1px solid var(--bl)",
            background: "rgba(74,158,255,0.06)",
            color: "var(--bl)",
            fontSize: 12,
            lineHeight: 1.6,
          }}
        >
          <span className="specimen" style={{ fontSize: 10, color: "var(--bl)" }}>
            ↻ RETRYING AFTER QUOTA BACKOFF
          </span>
          <div style={{ marginTop: 6, color: "var(--gr)" }}>{retrying}</div>
        </div>
      )}

      {/* gemini-2.5-pro 429 quota banner — explains WHY the pipeline stopped
          after claim_extractor instead of leaving a silent half-lit board. */}
      {quotaError && (
        <div
          style={{
            marginBottom: 16,
            padding: "10px 14px",
            border: "1px solid var(--y)",
            background: "rgba(255,200,0,0.06)",
            color: "var(--y)",
            fontSize: 12,
            lineHeight: 1.6,
          }}
        >
          <span className="specimen specimen-y" style={{ fontSize: 10 }}>
            ⚠ DRIFT_ANALYZER QUOTA LIMITED (429)
          </span>
          <div style={{ marginTop: 6, color: "var(--gr)" }}>{quotaError}</div>
        </div>
      )}

      {/* Horizontal pipeline */}
      <div
        style={{
          display: "flex",
          alignItems: "stretch",
          gap: 0,
          marginBottom: 22,
          overflowX: "auto",
          paddingBottom: 6,
        }}
      >
        {nodes.map((n, i) => (
          <div key={n.id} style={{ display: "flex", alignItems: "center" }}>
            <NodeColumn
              node={n}
              notifierSealed={notifierSealed}
              // A node sits at "idle" during the gap between the upstream node
              // finishing and its own first SSE event — for a single sub-agent
              // (e.g. citation_finder) the whole guarded stream is buffered and
              // arrives at once, so the node shows idle for its entire real work
              // window, then snaps to done. The pipeline runs strictly in order,
              // so once the previous node has begun and this one hasn't reached a
              // terminal state, this node IS the one working — render it active.
              // Gated on `running`: after run.complete we must NOT keep a node
              // "working", or memory_synthesizer (fire-and-forget — it may emit
              // no event this path) would hang at working forever.
              upstreamStarted={running && (i > 0 ? nodeStarted(nodes[i - 1]) : true)}
            />
            {i < nodes.length - 1 && <Connector />}
          </div>
        ))}
      </div>

      {/* Drift summary */}
      {drift && (
        <div
          style={{
            border: "1px solid var(--gr3)",
            background: "var(--bk2)",
            padding: "12px 14px",
            marginBottom: 18,
            maxWidth: 900,
          }}
        >
          <div className="specimen specimen-y" style={{ marginBottom: 6 }}>
            drift detected
          </div>
          <p style={{ fontSize: 12.5, lineHeight: 1.7, color: "var(--gr)" }}>{drift}</p>
        </div>
      )}

      {/* Email status */}
      {emails.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          <div className="specimen specimen-y" style={{ marginBottom: 8 }}>
            outgoing mail
          </div>
          {emails.map((e, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: 12,
                alignItems: "baseline",
                fontFamily: "var(--mono)",
                fontSize: 11.5,
                padding: "6px 0",
                borderBottom: "1px solid var(--gr3)",
              }}
            >
              <span
                style={{
                  color:
                    e.status === "sent" ? "var(--grn)" : e.status === "failed" ? "var(--rd)" : "var(--y)",
                  minWidth: 70,
                }}
              >
                {e.status === "sent" ? "✓ sent" : e.status === "failed" ? "✗ failed" : "→ sending"}
              </span>
              <span style={{ color: "var(--wh)" }}>{e.to}</span>
              <span style={{ color: "var(--gr)" }}>{e.error ?? e.subject}</span>
            </div>
          ))}
        </div>
      )}

      {/* Live SSE log */}
      <div style={{ border: "1px solid var(--gr3)", background: "var(--bk2)", padding: "12px 14px" }}>
        <div className="specimen specimen-y" style={{ marginBottom: 8 }}>
          live SSE event log
        </div>
        <div
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10.5,
            lineHeight: 1.7,
            color: "var(--gr)",
            maxHeight: 220,
            overflowY: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {log.length === 0 ? (
            <span style={{ opacity: 0.5 }}>
              {running ? (
                <span className="cd-running-row">
                  <span className="cd-spinner" />
                  connecting to supervisor pipeline…
                </span>
              ) : (
                "No events yet — enter your email and press “Run pipeline”."
              )}
            </span>
          ) : (
            <>
              {log.map((line, i) => (
                <div key={i} className="cd-log-line">
                  {line}
                </div>
              ))}
              {/* Keep a spinning "working" row at the bottom during the long
                  warm-up / first-inference / between-node silences so the live
                  log reads as in-progress instead of dumping a burst at the end. */}
              {running && (
                <div className="cd-running-row" style={{ marginTop: 4 }}>
                  <span className="cd-spinner" />
                  {status ?? "running…"}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function upsertEmail(list: EmailEvent[], subject: string, next: EmailEvent): EmailEvent[] {
  const idx = list.findIndex((e) => e.subject === subject && e.status === "sending");
  if (idx === -1) return [...list, next];
  const copy = [...list];
  copy[idx] = next;
  return copy;
}

function NodeColumn({
  node,
  notifierSealed,
  upstreamStarted,
}: {
  node: NodeView;
  notifierSealed?: boolean;
  upstreamStarted?: boolean;
}) {
  const laneKeys = Object.keys(node.lanes)
    .map(Number)
    .sort((a, b) => a - b);
  // The single-node "working" inference: this node hasn't emitted any event yet
  // (all lanes idle) but the upstream node has begun, so by the pipeline's
  // strict order this node is the one currently working. Fan-out nodes light
  // their own lanes as events stream in, so this only matters for single nodes.
  const inferWorking = upstreamStarted === true && !nodeStarted(node);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 178 }}>
      <div className="specimen specimen-y" style={{ fontSize: 9.5, letterSpacing: "0.1em", marginBottom: 2 }}>
        {node.label}
      </div>
      {/* notifier fans out one lane per affected citation. Rendering a stacked
          card per lane is noisy; collapse them into ONE card that shows a live
          count of alerts drafted. Other fan-out (claim_extractor ×2) is small
          enough to keep as separate lanes. */}
      {node.id === "notifier" ? (
        <NotifierCard lanes={node.lanes} sealed={notifierSealed} inferWorking={inferWorking} />
      ) : (
        laneKeys.map((k) => (
          <LaneCard key={k} lane={node.lanes[k]} inferWorking={inferWorking} />
        ))
      )}
    </div>
  );
}

// Collapsed notifier card: one box, live "drafting N…" → "✓ sent N alerts".
// We show ONLY the count of alerts actually drafted — no denominator. A
// denominator based on citation_finder's `total_found` stranded the card at
// "M/N" forever whenever OpenAlex reported N citing works but only M carried
// the DOI/author a notifier needs (the missing N−M lanes never light up). And
// once the denominator equals the lanes we see, it carries no information — so
// we drop it: a citation we couldn't notify simply isn't counted.
// `sealed` (memory_synthesizer has begun → no more notifier lanes are coming)
// is what lets us call the card DONE; until then it stays "drafting".
function NotifierCard({
  lanes,
  sealed,
  inferWorking,
}: {
  lanes: Record<number, NodeLane>;
  sealed?: boolean;
  inferWorking?: boolean;
}) {
  const all = Object.values(lanes);
  // Only count real notifier lanes — the seed lane 0 starts as {phase:"idle"}
  // before any citation arrives; treat it as "not yet started" so we show idle
  // until the first notifier event lights a lane.
  const started = all.filter((l) => l.phase !== "idle");
  const done = started.filter((l) => l.phase === "done").length;
  const errored = started.filter((l) => l.phase === "error").length;
  // The notifier has begun only once a real lane lights up.
  const hasStarted = started.length > 0;
  // Done = the fan-out is sealed (the downstream node began, so no further
  // lanes will appear) and no lane is still drafting. Without `sealed` the card
  // would flash "done" in the gap between two drafts.
  const allDone = hasStarted && sealed === true && done + errored >= started.length;

  // "working" covers the gap after citation_finder finishes but before the
  // first notifier lane streams in: the node is genuinely running (building
  // envelopes / awaiting the first draft), so show it active rather than idle.
  let phase: NodePhase = "idle";
  if (errored > 0 && done === 0) phase = "error";
  else if (allDone) phase = "done";
  else if (hasStarted || inferWorking === true) phase = "active";

  const color = phaseColor(phase);
  const active = phase === "active";

  let statusLabel: string;
  let body: React.ReactNode;
  if (!hasStarted && inferWorking === true) {
    statusLabel = "● running";
    body = (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <span className="cd-spinner" />
        <em>working…</em>
      </span>
    );
  } else if (!hasStarted) {
    statusLabel = "idle";
    body = "—";
  } else if (allDone) {
    statusLabel = "✓ done";
    body = (
      <>
        sent {done} alert{done === 1 ? "" : "s"}
        {errored > 0 ? ` · ${errored} failed` : ""}
      </>
    );
  } else {
    statusLabel = "● running";
    body = (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <span className="cd-spinner" />
        drafting {done} alert{done === 1 ? "" : "s"}…
      </span>
    );
  }

  return (
    <div
      style={{
        position: "relative",
        border: `1px solid ${color}`,
        background: "var(--bk2)",
        padding: "12px 12px 14px",
        minHeight: 92,
        transition: "border-color 0.3s",
        boxShadow: active ? "0 0 0 1px var(--y)" : "none",
      }}
    >
      <div className="corner-tl" />
      <div className="corner-tr" />
      <div className="corner-bl" />
      <div className="corner-br" />
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color,
          marginBottom: 6,
        }}
      >
        {statusLabel}
      </div>
      <div style={{ fontSize: 10.5, lineHeight: 1.5, color: "var(--gr)", wordBreak: "break-word" }}>
        {body}
      </div>
    </div>
  );
}

function LaneCard({ lane, inferWorking }: { lane: NodeLane; inferWorking?: boolean }) {
  // Show "working" when this lane is genuinely active, OR when it's still idle
  // but the pipeline says it's the node currently running (its events haven't
  // streamed in yet — see NodeColumn.inferWorking). A real terminal state
  // (done/error) always wins over the inference.
  const working = lane.phase === "active" || (lane.phase === "idle" && inferWorking === true);
  const color = working ? phaseColor("active") : phaseColor(lane.phase);
  const active = working;
  return (
    <div
      style={{
        position: "relative",
        border: `1px solid ${color}`,
        background: "var(--bk2)",
        padding: "12px 12px 14px",
        minHeight: 92,
        transition: "border-color 0.3s",
        boxShadow: active ? "0 0 0 1px var(--y)" : "none",
      }}
    >
      <div className="corner-tl" />
      <div className="corner-tr" />
      <div className="corner-bl" />
      <div className="corner-br" />
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color,
          marginBottom: 6,
        }}
      >
        {lane.phase === "done"
          ? "✓ done"
          : lane.phase === "error"
            ? "✗ error"
            : working
              ? "● running"
              : "idle"}
      </div>
      <div style={{ fontSize: 10.5, lineHeight: 1.5, color: "var(--gr)", wordBreak: "break-word" }}>
        {lane.error ??
          lane.summary ??
          (lane.action ? <em>{lane.action}…</em> : working ? <em>working…</em> : "—")}
      </div>
    </div>
  );
}

function Connector() {
  return (
    <div
      style={{
        width: 22,
        height: 1,
        background: "var(--gr3)",
        margin: "0 2px",
        alignSelf: "center",
        position: "relative",
        top: 8,
      }}
    >
      <span
        style={{
          position: "absolute",
          right: -3,
          top: -3,
          width: 6,
          height: 6,
          borderTop: "1px solid var(--gr3)",
          borderRight: "1px solid var(--gr3)",
          transform: "rotate(45deg)",
        }}
      />
    </div>
  );
}
