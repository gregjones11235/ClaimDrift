import { getPatterns } from "@/lib/api/client";
import { PatternList } from "@/components/features/PatternList";

export default async function PatternsPage() {
  const { items: patterns } = await getPatterns();

  const statCells = [
    { label: "Total patterns",  val: patterns.length,                                                            color: "var(--pu)" },
    { label: "Avg support",     val: patterns.length ? (patterns.reduce((s, p) => s + p.support_count, 0) / patterns.length).toFixed(1) : 0, color: "var(--y)" },
    { label: "Top type",        val: patterns.sort((a, b) => b.support_count - a.support_count)[0]?.pattern_type ?? "—", color: "var(--grn)" },
    { label: "Total events",    val: patterns.reduce((s, p) => s + p.support_count, 0),                         color: "var(--bl)" },
  ];

  return (
    <div style={{ maxWidth: 1100 }}>
      {/* Stats */}
      <div className="cd-stat-grid" style={{ gridTemplateColumns: "repeat(4,1fr)", marginBottom: 18 }}>
        {statCells.map(({ label, val, color }, i) => (
          <div key={i} className="cd-stat-cell">
            <style>{`.cd-stat-cell:nth-child(${i+1})::before { background: ${color}; }`}</style>
            <div className="specimen">{label}</div>
            <div className="cd-stat-val" style={{ color, fontSize: typeof val === "string" && val.length > 10 ? 14 : 26 }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Panel */}
      <div className="cd-panel">
        <div className="cd-panel-header">
          <span className="cd-panel-label">drift_patterns — memory loop base rates ⭐</span>
          <span className="specimen specimen-p">ELSER hybrid retrieval · memory_synthesizer agent</span>
        </div>
        <PatternList patterns={patterns} />

      </div>
    </div>
  );
}
