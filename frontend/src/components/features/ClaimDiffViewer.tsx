"use client";

import { ClaimDiff } from "@/types/claimdrift";

export function ClaimDiffViewer({ diff, index = 0 }: { diff: ClaimDiff; index?: number }) {
  const badgeColorMap: Record<string, string> = {
    numerical_shift:   "cd-badge-r",
    hedging_added:     "cd-badge-y",
    hedging_removed:   "cd-badge-b",
    claim_disappeared: "cd-badge-r",
    claim_added:       "cd-badge-g",
    claim_reversed:    "cd-badge-r",
  };

  const isDisappeared = diff.diff_type === "claim_disappeared";
  const isReversed    = diff.diff_type === "claim_reversed";

  return (
    <div className="cd-panel" style={{ marginBottom: 4 }}>
      <div className="cd-panel-header">
        <span className="cd-panel-label">claim_diff [{index}] — {diff.diff_type}</span>
        <span className="specimen">{diff.change_description}</span>
      </div>

      <div className="cd-diff-cols">
        {/* Preprint column */}
        <div className="cd-diff-col" style={{ background: isDisappeared || isReversed ? "var(--bk3)" : "var(--bk2)" }}>
          <div className="cd-diff-col-hdr">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <rect x="1" y="1" width="10" height="10" rx="1" stroke="var(--gr)" strokeWidth="1"/>
              <path d="M4 6h4M6 4v4" stroke="var(--gr)" strokeWidth="1" strokeLinecap="round"/>
            </svg>
            <span className="specimen">Preprint version · hedging: {diff.preprint_claim_id ? "none" : "—"}</span>
          </div>
          <div style={{ padding: 14 }}>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
              <span className={`cd-badge ${badgeColorMap[diff.diff_type] ?? ""}`}>{diff.diff_type}</span>
              {isDisappeared && <span className="cd-badge cd-badge-r">Retracted</span>}
              {isReversed    && <span className="cd-badge cd-badge-r">Reversed</span>}
            </div>
            <div className="cd-diff-text">{diff.preprint_text}</div>
          </div>
        </div>

        {/* Published column */}
        <div className="cd-diff-col cd-diff-col-hdr-pub" style={{ borderLeft: "1px solid var(--gr3)", background: isReversed ? "var(--bk3)" : "var(--bk2)" }}>
          <div className="cd-diff-col-hdr cd-diff-col-hdr-pub">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <rect x="1" y="1" width="10" height="10" rx="1" stroke="var(--y)" strokeWidth="1"/>
              <path d="M4 6h4" stroke="var(--y)" strokeWidth="1" strokeLinecap="round"/>
            </svg>
            <span className="specimen specimen-y">Published version · hedging: strong</span>
          </div>
          <div style={{ padding: 14 }}>
            {!isDisappeared ? (
              <>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                  <span className={`cd-badge ${badgeColorMap[diff.diff_type] ?? ""}`}>{diff.diff_type}</span>
                  {diff.diff_type === "hedging_added" && <span className="cd-badge cd-badge-y">hedging added</span>}
                </div>
                <div className="cd-diff-text">{diff.published_text}</div>
              </>
            ) : (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr)", fontStyle: "italic", padding: 20 }}>
                Claim removed in published version.
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Deviation footer */}
      <div style={{ padding: "9px 14px", borderTop: "1px solid var(--gr3)", display: "flex", alignItems: "center", gap: 12, background: "rgba(229,56,59,0.03)" }}>
        <span className="specimen specimen-r">Semantic deviation</span>
        <div style={{ flex: 1, height: 2, background: "var(--gr3)", position: "relative" }}>
          <div style={{ position: "absolute", left: 0, top: 0, height: "100%", background: "linear-gradient(90deg, var(--y), var(--rd))", width: "82%" }} />
        </div>
        <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--rd)", fontWeight: 700 }}>HIGH</span>
      </div>
    </div>
  );
}
