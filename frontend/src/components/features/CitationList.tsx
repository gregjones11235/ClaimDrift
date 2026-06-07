"use client";

import { useState } from "react";
import { AffectedCitation, SeverityTier } from "@/types/claimdrift";
import Link from "next/link";
import dayjs from "dayjs";

const TIER_COLORS: Record<SeverityTier, string> = {
  central:     "var(--rd)",
  comparative: "var(--or)",
  peripheral:  "var(--y)",
};

export function CitationList({ citations, eventId }: { citations: AffectedCitation[]; eventId: string }) {
  const [activeTier, setActiveTier] = useState<string>("all");

  const counts = {
    all:         citations.length,
    central:     citations.filter((c) => c.severity_tier === "central").length,
    comparative: citations.filter((c) => c.severity_tier === "comparative").length,
    peripheral:  citations.filter((c) => c.severity_tier === "peripheral").length,
  };

  const filtered = activeTier === "all" ? citations : citations.filter((c) => c.severity_tier === activeTier);

  const statCells = [
    { label: "Total blast radius",  val: counts.all,         color: "var(--rd)" },
    { label: "Central severity",    val: counts.central,     color: "var(--or)" },
    { label: "Comparative",         val: counts.comparative, color: "var(--y)"  },
    { label: "Peripheral",          val: counts.peripheral,  color: "var(--grn)" },
  ];

  return (
    <>
      {/* Stats */}
      <div className="cd-stat-grid" style={{ gridTemplateColumns: "repeat(4,1fr)", marginBottom: 18 }}>
        {statCells.map(({ label, val, color }, i) => (
          <div key={i} className="cd-stat-cell">
            <style>{`.cd-stat-cell:nth-child(${i+1})::before { background: ${color}; }`}</style>
            <div className="specimen">{label}</div>
            <div className="cd-stat-val" style={{ color }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Panel */}
      <div className="cd-panel">
        <div className="cd-panel-header">
          <span className="cd-panel-label">Affected citations — severity_tier</span>
          <span className="specimen">OpenAlex · ORCID-resolved · citation_finder agent</span>
        </div>

        {/* Filter tabs */}
        <div className="cd-filter-row">
          {(["all", "central", "comparative", "peripheral"] as const).map((tier) => (
            <button key={tier} className="cd-filter-tab"
              data-active={activeTier === tier ? "true" : "false"}
              onClick={() => setActiveTier(tier)}>
              {tier === "all" ? `All (${counts.all})` : `${tier.charAt(0).toUpperCase() + tier.slice(1)} (${counts[tier]})`}
            </button>
          ))}
        </div>

        {/* Citation cards */}
        <div>
          {filtered.map((cit) => (
            <div key={cit.affected_citation_id} style={{
              padding: "14px 16px",
              borderBottom: "1px solid var(--gr3)",
              borderLeft: `3px solid ${TIER_COLORS[cit.severity_tier]}`,
              transition: "background 0.15s",
              cursor: "default",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bk3)")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>

              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
                <div style={{ fontSize: 14, fontWeight: 500, color: "var(--wh2)", lineHeight: 1.45, flex: 1 }}>
                  {cit.citing_paper_title}
                </div>
                <span style={{
                  fontFamily: "var(--mono)", fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase",
                  padding: "3px 8px", border: `1px solid ${TIER_COLORS[cit.severity_tier]}`,
                  color: TIER_COLORS[cit.severity_tier], whiteSpace: "nowrap", flexShrink: 0,
                }}>
                  {cit.severity_tier}
                </span>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 8, flexWrap: "wrap" }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--bl)" }}>{cit.citing_paper_doi}</span>
                <span className="specimen">scored: {dayjs(cit.scored_at).format("YYYY-MM-DD")}</span>
              </div>

              <div style={{ fontSize: 13, fontWeight: 300, color: "var(--gr)", lineHeight: 1.6, padding: "8px 10px", background: "var(--bk3)", borderLeft: "2px solid var(--gr3)" }}>
                {cit.severity_reasoning}
              </div>

              {cit.citing_paper_authors.length > 0 && (
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                  {cit.citing_paper_authors.map((a, i) => (
                    <span key={i} style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr2)", padding: "2px 7px", border: "1px solid var(--gr3)" }}>
                      {a.name}{a.orcid ? <span style={{ color: "var(--grn)", marginLeft: 4 }}>⬡ {a.orcid.replace("https://orcid.org/", "")}</span> : ""}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div style={{ padding: "10px 16px", borderTop: "1px solid var(--gr3)", display: "flex", justifyContent: "space-between" }}>
          <span className="specimen">{counts.all} papers · 3 citation levels traversed</span>
          <span className="specimen specimen-b">openalex_citing_works tool</span>
        </div>
      </div>
    </>
  );
}
