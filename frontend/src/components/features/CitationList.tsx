"use client";

import { useState } from "react";
import { AffectedCitation } from "@/types/claimdrift";
import {
  CITES_COLOR,
  CITES_LABEL,
  CITES_ORDER,
  FOUND_VIA_LABEL,
  NOTIFY_PRIORITY_COLOR,
  ROLE_LABEL,
  labelOf,
} from "@/lib/labels";
import { CitesBadge, FlagBadges, VerificationBadge, VerifiedMark } from "./Badges";

// Citation analysis results, grouped by how each citing paper uses the revised claim (`cites`). An empty list is shown
// as such (the citation-analysis panel above says why: no numeric target, not run yet, or nothing found).
export function CitationList({ citations }: { citations: AffectedCitation[] }) {
  const [active, setActive] = useState<string>("all");

  const count = (k: string) => citations.filter((c) => c.cites === k).length;
  const classes = CITES_ORDER.filter((k) => count(k) > 0);
  const filtered = active === "all" ? citations : citations.filter((c) => c.cites === active);
  const pending = citations.filter((c) => c.review_status === "pending").length;

  const statCells = [
    { label: "Citing papers judged", val: citations.length, color: "var(--wh2)" },
    { label: "Use superseded value", val: count("superseded"), color: "var(--rd)" },
    { label: "Indirect / unclear", val: count("indirect") + count("unclear"), color: "var(--or)" },
    { label: "Provisional", val: pending, color: "var(--y)" },
  ];

  return (
    <>
      <div className="cd-stat-grid" style={{ gridTemplateColumns: "repeat(4,1fr)", marginBottom: 18 }}>
        {statCells.map(({ label, val, color }, i) => (
          <div key={i} className="cd-stat-cell">
            <style>{`.cd-stat-cell:nth-child(${i+1})::before { background: ${color}; }`}</style>
            <div className="specimen">{label}</div>
            <div className="cd-stat-val" style={{ color }}>{val}</div>
          </div>
        ))}
      </div>

      <div className="cd-panel">
        <div className="cd-panel-header">
          <span className="cd-panel-label">Affected citations — how each paper cites the revised claim</span>
          <span className="specimen">within open full-text papers</span>
        </div>

        <div className="cd-filter-row">
          <button className="cd-filter-tab" data-active={active === "all" ? "true" : "false"} onClick={() => setActive("all")}>
            All ({citations.length})
          </button>
          {classes.map((k) => (
            <button key={k} className="cd-filter-tab" data-active={active === k ? "true" : "false"} onClick={() => setActive(k)} title={CITES_LABEL[k]}>
              {k.replace(/_/g, " ")} ({count(k)})
            </button>
          ))}
        </div>

        <div>
          {citations.length === 0 && (
            <div style={{ padding: "18px 16px", fontSize: 13, color: "var(--gr)" }}>
              No citing paper has been judged for this event.
            </div>
          )}
          {filtered.map((cit) => {
            const color = CITES_COLOR[cit.cites ?? ""] ?? "var(--gr3)";
            return (
              <div key={cit.affected_citation_id} style={{ padding: "14px 16px", borderBottom: "1px solid var(--gr3)", borderLeft: `3px solid ${color}` }}>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
                  <div style={{ fontSize: 14, fontWeight: 500, color: "var(--wh2)", lineHeight: 1.45, flex: 1 }}>
                    {cit.citing_paper_title || cit.work_id || cit.affected_citation_id}
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                    <CitesBadge cites={cit.cites} />
                    {cit.role && <span className="cd-badge">{labelOf(ROLE_LABEL, cit.role)}</span>}
                    <VerificationBadge status={cit.review_status} />
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 8, flexWrap: "wrap" }}>
                  {cit.work_id && <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--bl)" }}>{cit.work_id}</span>}
                  {cit.citing_paper_doi && <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr)" }}>{cit.citing_paper_doi}</span>}
                  {cit.citing_paper_journal && <span className="specimen">{cit.citing_paper_journal}</span>}
                  {cit.citing_paper_date && <span className="specimen">{cit.citing_paper_date.slice(0, 10)}</span>}
                  {cit.found_via && <span className="specimen">found via: {labelOf(FOUND_VIA_LABEL, cit.found_via)}</span>}
                  {cit.relayed_by && <span className="specimen specimen-o">relayed by: {cit.relayed_by}</span>}
                  {cit.notify_priority && (
                    <span className="specimen" style={{ color: NOTIFY_PRIORITY_COLOR[cit.notify_priority] }}>notify priority: {cit.notify_priority}</span>
                  )}
                </div>

                {cit.sentence ? (
                  <div style={{ fontSize: 13, fontWeight: 300, color: "var(--gr)", lineHeight: 1.6, padding: "8px 10px", background: "var(--bk3)", borderLeft: "2px solid var(--gr3)", marginBottom: 6 }}>
                    &ldquo;{cit.sentence}&rdquo;
                    <div style={{ marginTop: 4 }}><VerifiedMark verified={cit.sentence_verified} /></div>
                  </div>
                ) : cit.citation_context ? (
                  <div style={{ fontSize: 13, fontWeight: 300, color: "var(--gr)", lineHeight: 1.6, padding: "8px 10px", background: "var(--bk3)", borderLeft: "2px solid var(--gr3)", marginBottom: 6 }}>
                    {cit.citation_context}
                  </div>
                ) : null}

                {cit.reason && (
                  <div style={{ fontSize: 12, color: "var(--gr)", lineHeight: 1.6, marginBottom: 6 }}>
                    <span className="specimen">reason: </span>{cit.reason}
                  </div>
                )}

                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <FlagBadges flags={cit.flags} />
                  {cit.unclear_rule && <span className="cd-badge cd-badge-y" style={{ textTransform: "none" }}>unclear rule {cit.unclear_rule}</span>}
                  {cit.judged_by && <span className="specimen" style={{ color: "var(--gr2)" }}>judged by {cit.judged_by}</span>}
                </div>
              </div>
            );
          })}
        </div>

        <div style={{ padding: "10px 16px", borderTop: "1px solid var(--gr3)", display: "flex", justifyContent: "space-between" }}>
          <span className="specimen">{citations.length} papers judged · recall applies within open full-text papers only</span>
          <span className="specimen specimen-b">Europe PMC open full text · up to 2 hops</span>
        </div>
      </div>
    </>
  );
}

