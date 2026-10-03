import { CitationAnalysis, CitationRun } from "@/types/claimdrift";
import { CITATION_ANALYSIS_LABEL, CITES_COLOR, CITES_ORDER, labelOf, shortDate, shortDateTime } from "@/lib/labels";
import { AutoRefresh } from "./AutoRefresh";
import { ColorBadge } from "./Badges";

const RUN_STATUS_COLOR: Record<string, string> = {
  queued: "var(--gr)",
  running: "var(--bl)",
  done: "var(--grn)",
  failed: "var(--rd)",
};

// Live progress of a running run: the pre-screen downloads citing full texts in small batches (a large target such as
// Guan takes minutes on a cold cache), so show how far it got instead of a silent "running".
function RunProgress({ run }: { run: CitationRun }) {
  const p = run.progress;
  if (!p) return <div className="specimen" style={{ marginTop: 4 }}>starting…</div>;
  const pct = p.phase === "prescreen" && p.to_screen ? Math.round((100 * (p.screened ?? 0)) / p.to_screen) : null;
  return (
    <div style={{ marginTop: 6, maxWidth: 240 }}>
      {pct != null && (
        <div style={{ height: 4, background: "var(--gr3)", marginBottom: 4 }}>
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--bl)" }} />
        </div>
      )}
      <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--bl)", lineHeight: 1.5 }}>{p.line}</div>
      {run.step_at && <div className="specimen" style={{ color: "var(--gr2)" }}>last step {shortDateTime(run.step_at)} · step {run.steps ?? 0}</div>}
    </div>
  );
}

// Citation-analysis status for one drift event. Coverage is reported honestly:
// any run that left candidate papers unjudged shows a visible warning, and
// recall is always qualified as "within open full-text papers".
export function CitationRunsPanel({
  analysis,
  runs,
  runsError,
}: {
  analysis?: CitationAnalysis | null;
  runs: CitationRun[] | null;
  runsError?: string | null;
}) {
  const hasRuns = (runs?.length ?? 0) > 0;
  if (!analysis && !hasRuns && !runsError) return null;

  const incompleteRuns = (runs ?? []).filter((r) => r.coverage_complete === false);
  const nUnjudged = incompleteRuns.reduce((s, r) => s + (r.n_unjudged ?? r.unjudged?.length ?? 0), 0);
  const unjudged = incompleteRuns.flatMap((r) => r.unjudged ?? []);
  const incomplete = incompleteRuns.length > 0 || analysis?.coverage_complete === false;
  // truncation notes of the runs (search cut off, screening limit, unjudged, unreadable full texts), deduplicated
  const notes = Array.from(new Set((runs ?? []).flatMap((r) => r.coverage_notes ?? [])));

  const active = (runs ?? []).some((r) => r.status === "running" || r.status === "queued");

  return (
    <div className="cd-panel" style={{ marginBottom: 16 }}>
      <AutoRefresh active={active} />
      <div className="cd-panel-header">
        <span className="cd-panel-label">Citation analysis</span>
        <span className="specimen">searches citing papers within open full-text papers only</span>
      </div>

      {analysis && (
        <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap", padding: "10px 16px", borderBottom: "1px solid var(--gr3)" }}>
          <ColorBadge color={RUN_STATUS_COLOR[analysis.status] ?? "var(--gr)"}>
            {labelOf(CITATION_ANALYSIS_LABEL, analysis.status)}
          </ColorBadge>
          {analysis.n_targets != null && <span className="specimen">{analysis.n_targets} target value(s)</span>}
          {analysis.n_superseded != null && (
            <span className="specimen" style={{ color: analysis.n_superseded > 0 ? "var(--rd)" : "var(--gr)" }}>
              {analysis.n_superseded} paper(s) using a superseded value
            </span>
          )}
          {analysis.checked_until && <span className="specimen">checked until {shortDate(analysis.checked_until)}</span>}
          {analysis.coverage_complete === true && <span className="specimen specimen-g">all candidates judged</span>}
        </div>
      )}

      {incomplete && (
        <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--gr3)", background: "rgba(245,197,24,0.06)", borderLeft: "3px solid var(--y)" }}>
          <div className="specimen specimen-y" style={{ marginBottom: 4 }}>⚠ coverage incomplete</div>
          <div style={{ fontSize: 13, color: "var(--wh2)", lineHeight: 1.6 }}>
            {nUnjudged > 0
              ? `${nUnjudged} candidate paper(s) were found but not judged, so the list below may miss papers that rely on the superseded value.`
              : "Not every citing paper within reach was examined, so the list below may miss papers that rely on the superseded value."}
          </div>
          {unjudged.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
              {unjudged.slice(0, 12).map((u, i) => (
                <span key={`${u.work_id}-${i}`}className="cd-badge" title={u.source ?? undefined} style={{ textTransform: "none" }}>
                  {u.work_id}{u.flags && u.flags.length ? ` · ${u.flags.join(",")}` : ""}
                </span>
              ))}
              {unjudged.length > 12 && <span className="specimen">+{unjudged.length - 12} more</span>}
            </div>
          )}
        </div>
      )}

      {notes.length > 0 && (
        <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--gr3)" }}>
          <div className="specimen" style={{ marginBottom: 4 }}>coverage</div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: "var(--gr)", lineHeight: 1.6 }}>
            {notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        </div>
      )}

      {hasRuns && (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--gr3)" }}>
              {["run", "status", "window", "candidates", "verdicts", "summary"].map((h) => (
                <th key={h} style={{ fontFamily: "var(--mono)", fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--gr2)", padding: "10px 16px", textAlign: "left", fontWeight: 400 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {runs!.map((r) => (
              <tr key={r.run_id} style={{ borderBottom: "1px solid var(--gr3)", verticalAlign: "top" }}>
                <td style={{ padding: "10px 16px", fontFamily: "var(--mono)", fontSize: 12, color: "var(--wh2)" }}>
                  {r.kind}
                  <div className="specimen" style={{ color: "var(--gr2)", marginTop: 2 }}>{shortDateTime(r.queued_at)}</div>
                </td>
                <td style={{ padding: "10px 16px" }}>
                  <ColorBadge color={RUN_STATUS_COLOR[r.status] ?? "var(--gr)"}>{r.status}</ColorBadge>
                  {r.status === "running" && <RunProgress run={r} />}
                  {r.error && <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--rd)", marginTop: 4, maxWidth: 220 }}>{r.error}</div>}
                </td>
                <td style={{ padding: "10px 16px", fontFamily: "var(--mono)", fontSize: 12, color: "var(--gr)" }}>
                  {r.since ? shortDate(r.since) : "start"} → {shortDate(r.checked_until)}
                </td>
                <td style={{ padding: "10px 16px", fontFamily: "var(--mono)", fontSize: 12, color: "var(--wh2)" }}>
                  {r.n_judged ?? "—"}/{r.n_candidates ?? "—"} judged
                  {r.coverage_complete === false && (
                    <div style={{ color: "var(--y)", marginTop: 2 }}>⚠ {r.n_unjudged ?? "?"} unjudged</div>
                  )}
                  {r.judged_by && (
                    <div className="specimen" style={{ color: "var(--gr2)", marginTop: 2 }}>
                      worker {r.judged_by.worker ?? 0} · batch {r.judged_by.batch_overflow ?? 0}
                    </div>
                  )}
                </td>
                <td style={{ padding: "10px 16px" }}>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {CITES_ORDER.filter((c) => (r.counts?.[c] ?? 0) > 0).map((c) => (
                      <span key={c} style={{ fontFamily: "var(--mono)", fontSize: 11, color: CITES_COLOR[c], border: `1px solid ${CITES_COLOR[c]}`, padding: "1px 6px" }}>
                        {c} {r.counts?.[c]}
                      </span>
                    ))}
                  </div>
                </td>
                <td style={{ padding: "10px 16px", fontSize: 13, color: "var(--gr)", fontWeight: 300, lineHeight: 1.5, maxWidth: 380 }}>
                  {r.summary || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!hasRuns && runsError && (
        <div style={{ padding: "10px 16px" }}>
          <span className="specimen">Run details unavailable: {runsError}</span>
        </div>
      )}
    </div>
  );
}
