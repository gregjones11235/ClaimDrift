import { ClaimDiff, ProvenanceRow } from "@/types/claimdrift";
import { FULLTEXT_TIER_COLOR, ROOT_CAUSE_LABEL, labelOf } from "@/lib/labels";
import { ColorBadge, VerifiedMark } from "./Badges";

const BADGE_COLOR: Record<string, string> = {
  numerical_shift:   "cd-badge-r",
  hedging_added:     "cd-badge-y",
  hedging_removed:   "cd-badge-b",
  claim_disappeared: "cd-badge-r",
  claim_added:       "cd-badge-g",
  claim_reversed:    "cd-badge-r",
  outcome_switch:    "cd-badge-o",
};

// `provenance` is the event-level provenance list; rows for this diff are
// matched by claim_diff_idx + quote to show each evidence quote's verbatim check.
export function ClaimDiffViewer({
  diff,
  index = 0,
  provenance,
}: {
  diff: ClaimDiff;
  index?: number;
  provenance?: ProvenanceRow[];
}) {
  const isDisappeared = diff.diff_type === "claim_disappeared";
  const isReversed    = diff.diff_type === "claim_reversed";
  const badge = BADGE_COLOR[diff.diff_type] ?? "";
  const evidence = diff.evidence ?? [];
  const provRows = (provenance ?? []).filter((p) => p.claim_diff_idx === index);

  const findProv = (quote: string, docId: string) =>
    provRows.find((p) => p.quote === quote && p.doc_id === docId) ?? provRows.find((p) => p.quote === quote);

  return (
    <div className="cd-panel" style={{ marginBottom: 4 }}>
      <div className="cd-panel-header" style={{ gap: 12 }}>
        <span className="cd-panel-label">claim_diff [{index}] — {diff.diff_type}</span>
        <span className="specimen" style={{ textAlign: "right" }}>{diff.change_description}</span>
      </div>

      {(diff.severity_tier || diff.root_cause || diff.materiality != null) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", padding: "10px 14px", borderBottom: "1px solid var(--gr3)" }}>
          {diff.severity_tier && (
            <ColorBadge color={FULLTEXT_TIER_COLOR[diff.severity_tier] ?? "var(--gr)"} title="Full-text tier for this change">
              full text: {diff.severity_tier}
            </ColorBadge>
          )}
          {diff.materiality != null && (
            <span className="specimen">materiality {diff.materiality.toFixed(2)}</span>
          )}
          {diff.root_cause && (
            <span className="cd-badge cd-badge-b" title={diff.root_cause}>
              root cause: {labelOf(ROOT_CAUSE_LABEL, diff.root_cause)}
            </span>
          )}
        </div>
      )}

      <div className="cd-diff-cols">
        {/* Preprint column */}
        <div className="cd-diff-col" style={{ background: isDisappeared || isReversed ? "var(--bk3)" : "var(--bk2)" }}>
          <div className="cd-diff-col-hdr">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <rect x="1" y="1" width="10" height="10" rx="1" stroke="var(--gr)" strokeWidth="1"/>
              <path d="M4 6h4M6 4v4" stroke="var(--gr)" strokeWidth="1" strokeLinecap="round"/>
            </svg>
            <span className="specimen">Preprint version</span>
          </div>
          <div style={{ padding: 14 }}>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
              <span className={`cd-badge ${badge}`}>{diff.diff_type}</span>
              {isDisappeared && <span className="cd-badge cd-badge-r">Removed</span>}
              {isReversed    && <span className="cd-badge cd-badge-r">Reversed</span>}
            </div>
            <div className="cd-diff-text">{diff.preprint_text || "—"}</div>
          </div>
        </div>

        {/* Published column */}
        <div className="cd-diff-col cd-diff-col-hdr-pub" style={{ borderLeft: "1px solid var(--gr3)", background: isReversed ? "var(--bk3)" : "var(--bk2)" }}>
          <div className="cd-diff-col-hdr cd-diff-col-hdr-pub">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <rect x="1" y="1" width="10" height="10" rx="1" stroke="var(--y)" strokeWidth="1"/>
              <path d="M4 6h4" stroke="var(--y)" strokeWidth="1" strokeLinecap="round"/>
            </svg>
            <span className="specimen specimen-y">Published version</span>
          </div>
          <div style={{ padding: 14 }}>
            {!isDisappeared ? (
              <>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                  <span className={`cd-badge ${badge}`}>{diff.diff_type}</span>
                  {diff.diff_type === "hedging_added" && <span className="cd-badge cd-badge-y">hedging added</span>}
                </div>
                <div className="cd-diff-text">{diff.published_text || "—"}</div>
              </>
            ) : (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr)", fontStyle: "italic", padding: 20 }}>
                Claim removed in published version.
              </div>
            )}
          </div>
        </div>
      </div>

      {diff.rationale && (
        <div style={{ padding: "10px 14px", borderTop: "1px solid var(--gr3)" }}>
          <div className="specimen" style={{ marginBottom: 4 }}>rationale</div>
          <div style={{ fontSize: 13, fontWeight: 300, color: "var(--gr)", lineHeight: 1.6 }}>{diff.rationale}</div>
        </div>
      )}

      {evidence.length > 0 && (
        <div style={{ padding: "10px 14px", borderTop: "1px solid var(--gr3)" }}>
          <div className="specimen" style={{ marginBottom: 6 }}>evidence quotes · checked verbatim against the source text</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {evidence.map((e, i) => {
              const prov = findProv(e.quote, e.doc_id);
              return (
                <div key={i} style={{ padding: "8px 10px", background: "var(--bk3)", borderLeft: `2px solid ${prov?.verified ? "var(--grn)" : prov ? "var(--rd)" : "var(--gr3)"}` }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap", marginBottom: 4 }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--bl)" }}>{e.doc_id}</span>
                    {(prov?.section || e.section) && <span className="specimen">§ {prov?.section || e.section}</span>}
                    {prov?.section_claimed && prov.section && prov.section_claimed !== prov.section && (
                      <span className="specimen specimen-y">claimed § {prov.section_claimed}</span>
                    )}
                    <span style={{ marginLeft: "auto" }}>
                      <VerifiedMark verified={prov ? prov.verified : null} label={prov ? undefined : "no provenance row"} />
                    </span>
                  </div>
                  <div style={{ fontSize: 13, color: "var(--wh2)", fontWeight: 300, lineHeight: 1.6 }}>&ldquo;{e.quote}&rdquo;</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {diff.label_problems && diff.label_problems.length > 0 && (
        <div style={{ padding: "8px 14px", borderTop: "1px solid var(--gr3)", background: "rgba(245,197,24,0.04)" }}>
          <span className="specimen specimen-y">output problems: </span>
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr)" }}>{diff.label_problems.join(" · ")}</span>
        </div>
      )}
    </div>
  );
}
