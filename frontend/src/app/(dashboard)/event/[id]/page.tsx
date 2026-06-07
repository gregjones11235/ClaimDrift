import { getDriftEvent, getAffectedCitations, getNotifications } from "@/lib/api/client";
import { ClaimDiffViewer } from "@/components/features/ClaimDiffViewer";
import { NumericalDeltaCard } from "@/components/features/NumericalDeltaCard";
import Link from "next/link";
import dayjs from "dayjs";

export default async function DriftDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [event, citationsResp, notificationsResp] = await Promise.all([
    getDriftEvent(id),
    getAffectedCitations(id),
    getNotifications(id),
  ]);

  const scoreColor = event.materiality_score >= 0.7 ? "var(--rd)"
    : event.materiality_score >= 0.5 ? "var(--y)"
    : "var(--grn)";

  const diffTypes = event.claim_diffs.map((d) => d.diff_type);
  const hasMemory = event.materiality_score !== undefined;

  return (
    <div>

      {/* Back */}
      <Link href="/dashboard" className="hover:text-[var(--y)] hover:border-[var(--gr3)]" style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        fontFamily: "var(--mono)", fontSize: 12, letterSpacing: "0.1em",
        textTransform: "uppercase", color: "var(--gr)", textDecoration: "none",
        marginBottom: 16, padding: "5px 10px", border: "1px solid transparent",
        transition: "all 0.15s",
      }}>
        ← Dashboard
      </Link>

      {/* Meta strip */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
        {diffTypes[0] && <span className={`cd-badge cd-badge-r`}>{diffTypes[0]}</span>}
        <span className="cd-badge">{event.preprint_version_compared} → published</span>
        <span className={`cd-badge ${event.materiality_score >= 0.7 ? "cd-badge-r" : "cd-badge-y"}`}>
          {event.materiality_score >= 0.7 ? "HIGH" : "MEDIUM"} SEVERITY
        </span>
        <span className="specimen" style={{ color: "var(--gr2)", marginLeft: "auto" }}>
          detected: {dayjs(event.detected_at).format("YYYY-MM-DD")}
        </span>
      </div>

      {/* Drift summary */}
      <div className="cd-panel" style={{ marginBottom: 16 }}>
        <div className="cd-panel-header">
          <span className="cd-panel-label">drift_summary</span>
          <span className="specimen">Gemini 2.5 Pro · drift_analyzer</span>
        </div>
        <div style={{ padding: "14px 16px", fontSize: 15, fontWeight: 300, color: "var(--wh2)", lineHeight: 1.75 }}>
          &ldquo;{event.drift_summary}&rdquo;
        </div>
      </div>

      {/* Materiality score panel */}
      <div className="cd-panel" style={{ marginBottom: 16 }}>
        <div className="cd-panel-header">
          <span className="cd-panel-label">materiality_score</span>
          <span className="specimen">semantic deviation severity · memory-calibrated</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "160px 1fr" }}>
          {/* Score side */}
          <div style={{ padding: "20px 16px", borderRight: "1px solid var(--gr3)", display: "flex", flexDirection: "column", justifyContent: "center" }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 52, fontWeight: 700, color: scoreColor, lineHeight: 1 }}>
              {event.materiality_score.toFixed(2)}
            </div>
            <div className="specimen specimen-r" style={{ marginBottom: 12 }}>materiality_score</div>
            {/* Memory calibration sub-stats */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <div>
                <div className="specimen" style={{ marginBottom: 2 }}>baseline</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 15, color: "var(--gr2)", fontWeight: 700 }}>—</div>
              </div>
              <div>
                <div className="specimen specimen-g" style={{ marginBottom: 2 }}>+memory</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 15, color: "var(--grn)", fontWeight: 700 }}>+0.14</div>
              </div>
            </div>
          </div>

          {/* Bar side */}
          <div style={{ padding: "18px 16px" }}>
            {/* Bar track */}
            <div style={{ height: 5, background: "var(--gr3)", position: "relative", marginBottom: 6 }}>
              <div style={{ display: "flex", height: "100%" }}>
                <div style={{ width: "33.3%", background: "rgba(62,207,142,.15)", borderRight: "1px solid var(--gr3)" }} />
                <div style={{ width: "33.3%", background: "rgba(245,197,24,.1)", borderRight: "1px solid var(--gr3)" }} />
                <div style={{ width: "33.4%", background: "rgba(229,56,59,.12)" }} />
              </div>
              {/* Marker */}
              <div style={{
                position: "absolute", top: -5, bottom: -5,
                left: `${event.materiality_score * 100}%`,
                width: 2, background: "var(--wh)", marginLeft: -1,
              }} />
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
              <span className="specimen specimen-g">0.0 minor</span>
              <span className="specimen">0.3</span>
              <span className="specimen" style={{ color: "var(--y)" }}>0.6 medium</span>
              <span className="specimen">0.9</span>
              <span className="specimen specimen-r">1.0 major</span>
            </div>

            {/* Diff type breakdown */}
            <div className="specimen" style={{ marginBottom: 8 }}>Diff breakdown</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {diffTypes.map((t, i) => (
                <span key={i} className={`cd-badge ${t === "numerical_shift" ? "cd-badge-r" : "cd-badge-y"}`}>{t}</span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Numerical delta + diffs */}
      {event.claim_diffs.map((diff, idx) => (
        <div key={idx} style={{ marginBottom: 16 }}>
          {diff.numerical_delta && <NumericalDeltaCard delta={diff.numerical_delta} />}
          <ClaimDiffViewer diff={diff} index={idx} />
        </div>
      ))}

      {/* Action bar */}
      <div style={{ display: "flex", gap: 8, marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--gr3)" }}>
        <button className="cd-btn">↓ Export PDF</button>
        <button className="cd-btn">✓ Mark reviewed</button>
        <Link href={`/event/${id}/citations`} className="cd-btn cd-btn-blue" style={{ marginLeft: "auto" }}>
          Citations ({citationsResp.count}) →
        </Link>
        <Link href={`/event/${id}/notifications`} className="cd-btn cd-btn-green">
          Notifications ({notificationsResp.count}) →
        </Link>
      </div>
    </div>
  );
}
