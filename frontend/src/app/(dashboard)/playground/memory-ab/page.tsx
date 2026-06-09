"use client";

import { useState } from "react";
import {
  runPlayground,
  StateView,
  RunMeta,
} from "@/lib/playground";
import { useRunGuard } from "@/lib/useRunGuard";

// The three demo states, fixed order. Mirrors apps/playground/server.py STATES.
const INITIAL_STATES: StateView[] = [
  { key: "none", label: "No memory", support: null, phase: "idle" },
  { key: "support5", label: "Memory · support=5", support: 5, phase: "idle" },
  { key: "support20", label: "Memory · support=20", support: 20, phase: "idle" },
];

// Color ramp for the calibrated score, matching the dashboard severity palette.
function scoreColor(v: number | null | undefined): string {
  if (v == null) return "var(--gr)";
  if (v >= 0.9) return "var(--rd)";
  if (v >= 0.7) return "var(--y)";
  if (v >= 0.4) return "var(--bl)";
  return "var(--grn)";
}

const PHASE_LABEL: Record<string, string> = {
  idle: "idle",
  injected: "memory staged",
  analyzing: "analyzing…",
  done: "complete",
};

export default function PlaygroundPage() {
  const [states, setStates] = useState<StateView[]>(INITIAL_STATES);
  const [meta, setMeta] = useState<RunMeta | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [abort, setAbort] = useState<(() => void) | null>(null);

  // Warn before navigating away mid-run — unmounting kills the live SSE stream
  // and resets the three-state board. See useRunGuard.
  useRunGuard(
    running,
    "The A/B comparison is still running. Leaving this page will stop it and reset the board. Leave anyway?",
  );

  function patch(key: string, next: Partial<StateView>) {
    setStates((cur) => cur.map((s) => (s.key === key ? { ...s, ...next } : s)));
  }

  function start() {
    setStates(INITIAL_STATES);
    setMeta(null);
    setError(null);
    setLog([]);
    setRunning(true);

    const stop = runPlayground(
      (type, data) => {
        setLog((l) => [...l, `${type}  ${JSON.stringify(data).slice(0, 120)}`]);
        const key = data.key as string | undefined;
        switch (type) {
          case "run.started":
            setMeta(data as unknown as RunMeta);
            break;
          case "state.started":
            if (key) patch(key, { phase: "idle" });
            break;
          case "state.injected":
            if (key)
              patch(key, {
                phase: "injected",
                injectDetail: data.detail as string,
              });
            break;
          case "state.analyzing":
            if (key) patch(key, { phase: "analyzing" });
            break;
          case "state.done":
            if (key)
              patch(key, {
                phase: "done",
                calibrated_materiality: data.calibrated_materiality as number | null,
                baseline_materiality_without_memory:
                  data.baseline_materiality_without_memory as number | null,
                materiality_score: data.materiality_score as number | null,
                retrieved_patterns_used:
                  (data.retrieved_patterns_used as string[]) ?? [],
                memory_pattern_ids: (data.memory_pattern_ids as string[]) ?? [],
                rationale: data.rationale as string | null,
                events: data.events as number,
                quota_error: data.quota_error as
                  | "quota_confirmed"
                  | "quota_suspected"
                  | undefined,
                quota_detail: data.quota_detail as string | undefined,
              });
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
    <div style={{ maxWidth: 1100 }}>
      {/* Header / intro */}
      <div style={{ marginBottom: 18 }}>
        <h1
          style={{
            fontFamily: "var(--display)",
            fontSize: 28,
            color: "var(--wh)",
            letterSpacing: "0.01em",
            // Syne 800's descenders (g/y/p) overshoot the em-box; lineHeight
            // alone doesn't reserve room for the glyph ink, so the "g" tail gets
            // clipped. block display + generous lineHeight + real paddingBottom
            // give the descender actual pixels below the baseline.
            display: "block",
            lineHeight: 1.4,
            paddingBottom: 8,
            marginBottom: 8,
            overflow: "visible",
          }}
        >
          Memory-Loop A/B Playground
        </h1>
        <p
          style={{
            fontSize: 13,
            lineHeight: 1.7,
            color: "var(--gr)",
            maxWidth: 720,
          }}
        >
          The same real, ambiguous outcome switch case is run through
          the production <span style={{ color: "var(--y)" }}>drift_analyzer</span>{" "}
          three times — with no memory, then with a staged base rate of 5 and 20
          prior cases. The agent reads <code style={{ color: "var(--y)" }}>support_count</code>{" "}
          from Elasticsearch itself; we never feed it the number. Watch how
          severity calibration — and its reasoning — converge with the base rate.
        </p>

        {/* What the case is — spelled out so the registry/DOI codes aren't opaque. */}
        <div
          style={{
            marginTop: 14,
            padding: "12px 14px",
            border: "1px solid var(--gr3)",
            background: "var(--bk2)",
            maxWidth: 720,
            fontSize: 12,
            lineHeight: 1.7,
            color: "var(--gr)",
          }}
        >
          <div className="specimen specimen-y" style={{ marginBottom: 6 }}>
            the case
          </div>
          <strong style={{ color: "var(--wh)", fontWeight: 600 }}>
            Tasimelteon SET trial for Non-24-Hour Sleep–Wake Disorder
          </strong>{" "}
          (Lockley et al., <em>The Lancet</em>, 2015). A pre-registered co-primary
          endpoint was quietly demoted to a subordinate &ldquo;step-down&rdquo; endpoint
          between the trial&rsquo;s registration and its published paper — a textbook
          outcome switch.
          <div style={{ marginTop: 8, fontSize: 11 }}>
            <a
              href="https://clinicaltrials.gov/study/NCT01163032"
              target="_blank"
              rel="noopener"
              style={{ color: "var(--bl)", textDecorationLine: "none" }}
            >
              NCT01163032
            </a>{" "}
            <span style={{ opacity: 0.7 }}>
              — the trial&rsquo;s ClinicalTrials.gov registry ID (what was promised)
            </span>
            <br />
            <a
              href="https://doi.org/10.1016/S0140-6736(15)60031-9"
              target="_blank"
              rel="noopener"
              style={{ color: "var(--bl)", textDecorationLine: "none" }}
            >
              10.1016/S0140-6736(15)60031-9
            </a>{" "}
            <span style={{ opacity: 0.7 }}>
              — the DOI of the published Lancet paper (what was delivered)
            </span>
          </div>
        </div>
      </div>

      {/* Run control */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          marginBottom: 20,
        }}
      >
        <button
          onClick={running ? stop : start}
          style={{
            background: running ? "transparent" : "var(--y)",
            color: running ? "var(--rd)" : "var(--bk)",
            border: running ? "1px solid var(--rd)" : "1px solid var(--y)",
            padding: "12px 26px",
            fontFamily: "var(--mono)",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            cursor: "pointer",
            transition: "all 0.2s",
          }}
        >
          {running ? "■ Stop run" : "▶ Run A/B comparison"}
        </button>
        {meta?.case && (
          <span className="specimen" style={{ color: "var(--gr)" }}>
            registry {meta.case.registry_id} → published {meta.case.published_doi}
          </span>
        )}
        {error && (
          <span className="specimen" style={{ color: "var(--rd)" }}>
            ⚠ {error}
          </span>
        )}
      </div>

      {/* Three-state comparison */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 14,
          marginBottom: 22,
        }}
      >
        {states.map((s) => (
          <StateCard key={s.key} s={s} />
        ))}
      </div>

      {/* Raw event log (skeleton — gives proof the SSE is live) */}
      <div
        style={{
          border: "1px solid var(--gr3)",
          background: "var(--bk2)",
          padding: "12px 14px",
        }}
      >
        <div className="specimen specimen-y" style={{ marginBottom: 8 }}>
          live SSE event log
        </div>
        <div
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10.5,
            lineHeight: 1.7,
            color: "var(--gr)",
            maxHeight: 200,
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
                  connecting to live drift_analyzer…
                </span>
              ) : (
                "No events yet — press “Run A/B comparison”."
              )}
            </span>
          ) : (
            <>
              {log.map((line, i) => (
                <div key={i} className="cd-log-line">
                  {line}
                </div>
              ))}
              {/* While the run is live, keep a spinning "working" row pinned to
                  the bottom so the ~15s gaps between frames read as in-progress
                  rather than frozen. */}
              {running && (
                <div className="cd-running-row" style={{ marginTop: 4 }}>
                  <span className="cd-spinner" />
                  running…
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function StateCard({ s }: { s: StateView }) {
  const score = s.calibrated_materiality;
  const active = s.phase === "analyzing";
  const done = s.phase === "done";
  return (
    <div
      style={{
        position: "relative",
        border: `1px solid ${active ? "var(--y)" : "var(--gr3)"}`,
        background: "var(--bk2)",
        padding: 18,
        minHeight: 230,
        transition: "border-color 0.3s",
      }}
    >
      <div className="corner-tl" />
      <div className="corner-tr" />
      <div className="corner-bl" />
      <div className="corner-br" />

      {/* header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: 14,
        }}
      >
        <span
          className="specimen specimen-y"
          style={{ fontSize: 10, letterSpacing: "0.1em" }}
        >
          {s.label}
        </span>
        <span
          className="specimen"
          style={{
            color: active
              ? "var(--y)"
              : done
                ? "var(--grn)"
                : "var(--gr)",
          }}
        >
          {PHASE_LABEL[s.phase]}
        </span>
      </div>

      {/* score */}
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 48,
          fontWeight: 700,
          lineHeight: 1,
          color: scoreColor(score),
          marginBottom: 6,
        }}
      >
        {score != null ? (
          score.toFixed(2)
        ) : active ? (
          // ~15s live drift_analyzer call — show a spinner so the judge sees the
          // run is in flight, not stalled, while the score is computed.
          <span style={{ display: "inline-flex", alignItems: "center", gap: 12, fontSize: 20 }}>
            <span className="cd-spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
            <span style={{ color: "var(--y)", letterSpacing: "0.04em" }}>analyzing…</span>
          </span>
        ) : (
          "—"
        )}
      </div>
      <div className="specimen" style={{ marginBottom: 14 }}>
        calibrated materiality
      </div>

      {/* gemini-2.5-pro 429 quota notice — explicit, never a blank score */}
      {s.quota_error && (
        <div
          style={{
            border: "1px solid var(--y)",
            background: "rgba(255,200,0,0.06)",
            padding: "8px 10px",
            marginBottom: 14,
            fontSize: 11,
            lineHeight: 1.55,
            color: "var(--y)",
          }}
        >
          <span className="specimen specimen-y" style={{ fontSize: 9.5 }}>
            {s.quota_error === "quota_confirmed"
              ? "⚠ QUOTA LIMITED (429 — confirmed)"
              : "⚠ QUOTA LIMITED (429 — suspected)"}
          </span>
          <div style={{ marginTop: 5, color: "var(--gr)" }}>
            {s.quota_detail ??
              "drift_analyzer (gemini-2.5-pro) hit Vertex shared-quota; retry shortly."}
          </div>
        </div>
      )}

      {/* support + retrieved */}
      <div style={{ display: "flex", gap: 18, marginBottom: 14 }}>
        <div>
          <div className="specimen">support_count</div>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 16,
              color: s.support == null ? "var(--gr)" : "var(--wh)",
            }}
          >
            {s.support == null ? "none" : s.support}
          </div>
        </div>
        <div>
          <div className="specimen">retrieved</div>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 16,
              color: (s.memory_pattern_ids?.length ?? 0) > 0 ? "var(--grn)" : "var(--gr)",
            }}
          >
            {s.memory_pattern_ids?.length ?? 0}
          </div>
        </div>
      </div>

      {/* rationale */}
      {s.rationale && (
        <p
          style={{
            fontSize: 11.5,
            lineHeight: 1.6,
            color: "var(--gr)",
            borderTop: "1px solid var(--gr3)",
            paddingTop: 10,
          }}
        >
          {s.rationale}
        </p>
      )}
    </div>
  );
}
