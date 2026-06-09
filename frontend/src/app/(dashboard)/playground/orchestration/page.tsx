"use client";

import { useMemo, useState } from "react";
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

  // The notifier fans out one lane per affected citation, but lanes appear one
  // at a time AS each notification is drafted — so a lane-only count makes the
  // denominator climb with the numerator (1/1, 2/2, …). The true total is what
  // citation_finder reported ("found N affected citation(s)"); parse N from its
  // summary and use it as the fixed denominator for the notifier progress.
  const expectedAlerts = useMemo(() => {
    const cf = nodes.find((n) => n.id === "citation_finder");
    const summary = cf?.lanes[0]?.summary ?? "";
    const m = summary.match(/found\s+(\d+)\s+affected/i);
    return m ? Number(m[1]) : 0;
  }, [nodes]);

  // The supervisor pipeline runs ~200s; warn before any navigation that would
  // unmount this page and kill the live SSE run. See useRunGuard.
  useRunGuard(
    running,
    "The 5-agent pipeline is still running (~200s). Leaving this page will stop it and you won't get the drift-alert email. Leave anyway?",
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
            &ldquo;The NO Answer for Autism Spectrum Disorder&rdquo;
          </strong>{" "}
          (bioRxiv 2023 → <em>Advanced Science</em>). Between the preprint and the
          published version, a strong causal claim — that treating mice with a
          nitric-oxide donor <em>led to an autism-like phenotype</em> — was removed,
          and &ldquo;NO plays a <em>pathological</em> role in ASD&rdquo; was softened to a
          &ldquo;<em>significant</em> role.&rdquo; The pipeline detects this drift and notifies the{" "}
          <span style={{ color: "var(--y)" }}>real papers that cite the preprint</span>.
          <div style={{ marginTop: 8, fontSize: 11 }}>
            <a
              href="https://doi.org/10.1101/2023.01.07.523095"
              target="_blank"
              rel="noopener"
              style={{ color: "var(--bl)", textDecorationLine: "none" }}
            >
              10.1101/2023.01.07.523095
            </a>{" "}
            <span style={{ opacity: 0.7 }}>— the bioRxiv preprint (what was claimed)</span>
            <br />
            <a
              href="https://doi.org/10.1002/advs.202205783"
              target="_blank"
              rel="noopener"
              style={{ color: "var(--bl)", textDecorationLine: "none" }}
            >
              10.1002/advs.202205783
            </a>{" "}
            <span style={{ opacity: 0.7 }}>
              — the published Advanced Science paper (what was delivered)
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
            <NodeColumn node={n} expectedTotal={expectedAlerts} />
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

function NodeColumn({ node, expectedTotal }: { node: NodeView; expectedTotal?: number }) {
  const laneKeys = Object.keys(node.lanes)
    .map(Number)
    .sort((a, b) => a - b);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 178 }}>
      <div className="specimen specimen-y" style={{ fontSize: 9.5, letterSpacing: "0.1em", marginBottom: 2 }}>
        {node.label}
      </div>
      {/* notifier fans out one lane per affected citation (up to ~13). Rendering
          13 stacked cards is noisy; collapse them into ONE card that shows a
          live N/total drafting progress instead. Other fan-out (claim_extractor
          ×2) is small enough to keep as separate lanes. */}
      {node.id === "notifier" ? (
        <NotifierCard lanes={node.lanes} expectedTotal={expectedTotal} />
      ) : (
        laneKeys.map((k) => <LaneCard key={k} lane={node.lanes[k]} />)
      )}
    </div>
  );
}

// Collapsed notifier card: one box, live "drafting N/total" → "✓ sent total".
// `expectedTotal` is the affected-citation count citation_finder reported; it's
// the FIXED denominator so progress reads 1/13 → 13/13 instead of 1/1 → 13/13
// (lanes appear one-per-draft, so a lane-only count moves the denominator too).
function NotifierCard({
  lanes,
  expectedTotal,
}: {
  lanes: Record<number, NodeLane>;
  expectedTotal?: number;
}) {
  const all = Object.values(lanes);
  // Only count real notifier lanes — the seed lane 0 starts as {phase:"idle"}
  // before any citation arrives; treat it as "not yet started" so we show idle
  // (not 0/1) until the first notifier event lights a lane.
  const started = all.filter((l) => l.phase !== "idle");
  const done = started.filter((l) => l.phase === "done").length;
  const errored = started.filter((l) => l.phase === "error").length;
  const settled = done + errored;
  // Denominator: prefer citation_finder's reported count; fall back to the
  // number of lanes we've seen (covers a run where that summary is missing).
  // Never let it drop below settled — a late/low count shouldn't show 13/12.
  const total = Math.max(expectedTotal ?? 0, started.length, settled);
  // The notifier itself has begun only once a real lane lights up — citation_finder
  // reporting N must NOT flip this card to "running 0/N" before the fan-out starts.
  const hasStarted = started.length > 0;
  const allDone = hasStarted && settled >= total;

  let phase: NodePhase = "idle";
  if (errored > 0 && done === 0) phase = "error";
  else if (allDone) phase = "done";
  else if (hasStarted) phase = "active";

  const color = phaseColor(phase);
  const active = phase === "active";

  let statusLabel: string;
  let body: React.ReactNode;
  if (!hasStarted) {
    statusLabel = "idle";
    body = "—";
  } else if (allDone) {
    statusLabel = "✓ done";
    body = (
      <>
        drafted {done}/{total} alert{total === 1 ? "" : "s"}
        {errored > 0 ? ` · ${errored} failed` : ""}
      </>
    );
  } else {
    statusLabel = "● running";
    body = (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <span className="cd-spinner" />
        drafting {done}/{total} alerts…
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

function LaneCard({ lane }: { lane: NodeLane }) {
  const color = phaseColor(lane.phase);
  const active = lane.phase === "active";
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
        {lane.phase === "active"
          ? "● running"
          : lane.phase === "done"
            ? "✓ done"
            : lane.phase === "error"
              ? "✗ error"
              : "idle"}
      </div>
      <div style={{ fontSize: 10.5, lineHeight: 1.5, color: "var(--gr)", wordBreak: "break-word" }}>
        {lane.error ?? lane.summary ?? (lane.action ? <em>{lane.action}…</em> : "—")}
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
