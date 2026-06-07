import { getDriftEvents, getStats } from "@/lib/api/client";
import Link from "next/link";
import dayjs from "dayjs";

import Oscilloscope from "@/components/landing/Oscilloscope";

export default async function DashboardPage() {
  const [stats, { items: events }] = await Promise.all([
    getStats(),
    getDriftEvents(),
  ]);

  if (stats.drift_events_total === 0) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 240, fontFamily: "var(--mono)", fontSize: 14, color: "var(--gr)", fontStyle: "italic" }}>
        No drift events yet. Run the pipeline to begin.
      </div>
    );
  }

  const statCells = [
    { label: "Tracked events",      val: stats.drift_events_total,                          sub: `${stats.high_severity_count} high severity (≥0.7)`, color: "var(--y)",  oscColor: "rgba(245,197,24,.35)" },
    { label: "Avg materiality_score", val: stats.avg_materiality_score.toFixed(2),           sub: `across ${stats.drift_events_total} events`,          color: "var(--rd)", oscColor: "rgba(229,56,59,.4)" },
    { label: "Affected citations",   val: stats.affected_citations_total,                    sub: `${stats.notifications_sent}/${stats.notifications_total} emails sent`,   color: "var(--grn)", oscColor: "rgba(62,207,142,.4)" },
    { label: "Patterns learned",     val: stats.patterns_total,                              sub: stats.top_pattern_type ?? "—",                        color: "var(--bl)", oscColor: "rgba(74,158,255,.4)" },
  ];

  return (
    <div>
      {/* Stat cards */}
      <div className="cd-stat-grid" style={{ gridTemplateColumns: "repeat(4,1fr)", marginBottom: 18 }}>
        {statCells.map(({ label, val, sub, color, oscColor }, i) => (
          <div key={i} className="cd-stat-cell" style={{ ["--accent" as any]: color }}>
            <style>{`.cd-stat-cell:nth-child(${i + 1})::before { background: ${color}; }`}</style>
            <div className="specimen">{label}</div>
            <div className="cd-stat-val" style={{ color }}>{val}</div>
            <div className="specimen" style={{ color: "var(--gr2)" }}>{sub}</div>
            <div style={{ marginTop: 20 }}>
              <Oscilloscope color={oscColor} height={40} speed={4 + i} />
            </div>
          </div>
        ))}
      </div>

      {/* Events table */}
      <div className="cd-panel">
        <div className="cd-panel-header">
          <span className="cd-panel-label">Drift events</span>
          <span className="specimen">{events.length} events · sorted by materiality_score desc</span>
        </div>

        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--gr3)" }}>
              {["drift_summary", "preprint_doi → published_doi", "materiality_score", "diff_type", "detected_at", ""].map((h) => (
                <th key={h} style={{
                  fontFamily: "var(--mono)", fontSize: 13, letterSpacing: "0.1em",
                  textTransform: "uppercase", color: "var(--gr2)",
                  padding: "14px 20px", textAlign: "left", fontWeight: 400,
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {events.map((event) => {
              const diffType = event.claim_diffs?.[0]?.diff_type ?? "—";
              const scoreColor = event.materiality_score >= 0.7 ? "var(--rd)"
                : event.materiality_score >= 0.5 ? "var(--y)"
                : "var(--grn)";
              return (
                <tr key={event.event_id}
                  className="hover:bg-[rgba(245,197,24,0.03)]"
                  style={{ borderBottom: "1px solid var(--gr3)", cursor: "pointer", transition: "background 0.15s", position: "relative" }}>

                  <td style={{ padding: "16px 20px", fontSize: 15, color: "var(--wh2)", fontWeight: 300, lineHeight: 1.6, maxWidth: 450 }}>
                    <div style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                      {event.drift_summary}
                    </div>
                  </td>

                  <td style={{ padding: "16px 20px" }}>
                    <Link href={`/event/${event.event_id}`} style={{ position: "absolute", inset: 0, zIndex: 10 }} />
                    <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr)" }}>{event.preprint_doi}</div>
                    <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--bl)", marginTop: 4 }}>↳ {event.published_doi}</div>
                  </td>

                  <td style={{ padding: "16px 20px", textAlign: "center" }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 18, fontWeight: 700, color: scoreColor }}>
                      {event.materiality_score.toFixed(2)}
                    </span>
                  </td>

                  <td style={{ padding: "16px 20px" }}>
                    <span className="cd-badge">{diffType}</span>
                  </td>

                  <td style={{ padding: "16px 20px", fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)" }}>
                    {dayjs(event.detected_at).format("YYYY-MM-DD")}
                  </td>

                  <td style={{ padding: "16px 20px", fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)" }}>
                    View →
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {stats.drift_events_total > events.length && (
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--gr3)" }}>
            <span className="specimen">Showing {events.length} most-recent of {stats.drift_events_total} events</span>
          </div>
        )}
      </div>
    </div>
  );
}
