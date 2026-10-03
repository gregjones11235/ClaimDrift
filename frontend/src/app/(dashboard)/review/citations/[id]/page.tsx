import Link from "next/link";
import { bffErrorMessage, getReviewCitation } from "@/lib/api/client";
import { ReviewCitationDetail } from "@/types/claimdrift";
import { BffNotice } from "@/components/features/BffNotice";
import { CitesBadge, ColorBadge, ReasonBadges, ReviewStatusBadge, VerifiedMark } from "@/components/features/Badges";
import { SeverityPair } from "@/components/features/SeverityPanels";
import { ReviewForm } from "@/components/features/ReviewForm";
import {
  FLAG_EXPLANATION,
  FOUND_VIA_LABEL,
  NOTIFY_PRIORITY_COLOR,
  ROLE_LABEL,
  ROOT_CAUSE_LABEL,
  UNCLEAR_RULE_EXPLANATION,
  labelOf,
  shortDate,
} from "@/lib/labels";

const backLink: React.CSSProperties = {
  display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--mono)", fontSize: 12,
  letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--gr)", textDecoration: "none", marginBottom: 16,
};

// The three conditions the verifier checks before a "superseded" verdict stands
// (claimdrift/citations/orchestra.py VERIFY_SYSTEM).
const CONDITIONS: [key: "condition_1" | "condition_2" | "condition_3", text: string][] = [
  ["condition_1", "The paper's reference list contains the target preprint"],
  ["condition_2", "The old value is attributed to the target (citation on that or the adjacent sentence)"],
  ["condition_3", "The value is used as the target's finding or a parameter — not flagged as earlier, current value not given alongside"],
];

function pmcUrl(workId: string): string | null {
  return /^PMC\d+$/i.test(workId) ? `https://pmc.ncbi.nlm.nih.gov/articles/${workId.toUpperCase()}/` : null;
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "baseline", padding: "6px 0", borderBottom: "1px solid var(--gr3)" }}>
      <span className="specimen" style={{ width: 130, flexShrink: 0 }}>{k}</span>
      <span style={{ fontSize: 13, color: "var(--wh2)", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>{children}</span>
    </div>
  );
}

export default async function ReviewCitationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let acId = id;
  try {
    acId = decodeURIComponent(id);
  } catch {
    /* already decoded */
  }
  let data: ReviewCitationDetail | null = null;
  let error: string | null = null;
  try {
    data = await getReviewCitation(acId);
  } catch (e) {
    error = bffErrorMessage(e);
  }

  if (!data) {
    return (
      <div>
        <Link href="/review?kind=citations" style={backLink}>← Review queue</Link>
        <BffNotice message={error ?? "Citation not found."} />
      </div>
    );
  }

  const { citation: c, event, claim_diff: diff, drift_summary } = data;
  const workUrl = c.work_id ? pmcUrl(c.work_id) : null;
  const ver = c.verification;
  const flags = c.flags ?? [];

  return (
    <div>
      <Link href="/review?kind=citations" style={backLink}>← Review queue</Link>

      {/* Citing paper */}
      <div className="cd-panel" style={{ marginBottom: 16 }}>
        <div className="cd-panel-header">
          <span className="cd-panel-label">Citation verdict review</span>
          <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <ReviewStatusBadge status={c.review_status} />
          </span>
        </div>
        <div style={{ padding: "14px 16px" }}>
          <div style={{ fontSize: 17, color: "var(--wh)", lineHeight: 1.45, marginBottom: 6 }}>
            {c.citing_paper_title || c.work_id || c.affected_citation_id}
          </div>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "baseline", marginBottom: 10 }}>
            {c.work_id && (workUrl
              ? <a href={workUrl} target="_blank" rel="noopener" style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--bl)", textDecoration: "none" }}>{c.work_id} ↗</a>
              : <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--bl)" }}>{c.work_id}</span>)}
            {c.citing_paper_doi && <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--gr)" }}>{c.citing_paper_doi}</span>}
            {c.citing_paper_journal && <span className="specimen">{c.citing_paper_journal}</span>}
            {c.citing_paper_date && <span className="specimen">{shortDate(c.citing_paper_date)}</span>}
          </div>
          {(c.citing_paper_authors?.length ?? 0) > 0 && (
            <div style={{ fontSize: 12, color: "var(--gr)", marginBottom: 10 }}>
              {c.citing_paper_authors!.map((a) => a.name).join(", ")}
            </div>
          )}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <CitesBadge cites={c.cites} />
            {c.role && <span className="cd-badge">{labelOf(ROLE_LABEL, c.role)}</span>}
            {c.notify_priority && (
              <ColorBadge color={NOTIFY_PRIORITY_COLOR[c.notify_priority] ?? "var(--gr)"}>notify priority: {c.notify_priority}</ColorBadge>
            )}
            <ReasonBadges reasons={c.review_reasons} />
          </div>
        </div>
      </div>

      {/* The drift it concerns */}
      <div className="cd-panel" style={{ marginBottom: 16 }}>
        <div className="cd-panel-header">
          <span className="cd-panel-label">The revised claim</span>
          {event && (
            <Link href={`/review/events/${event.event_id}`} className="specimen specimen-b" style={{ textDecoration: "none" }}>
              {event.title || event.preprint_doi} →
            </Link>
          )}
        </div>
        {event && (
          <div style={{ display: "flex", gap: 20, alignItems: "flex-start", padding: "10px 16px", borderBottom: "1px solid var(--gr3)" }}>
            <SeverityPair abstractClass={event.abstract_class} fulltextTier={event.fulltext_tier} />
            {drift_summary && <div style={{ fontSize: 13, fontWeight: 300, color: "var(--gr)", lineHeight: 1.6 }}>{drift_summary}</div>}
          </div>
        )}
        {diff ? (
          <>
            <div className="cd-diff-cols">
              <div className="cd-diff-col" style={{ background: "var(--bk3)" }}>
                <div className="cd-diff-col-hdr"><span className="specimen specimen-r">Old value · preprint (superseded)</span></div>
                <div style={{ padding: 14 }} className="cd-diff-text">{diff.preprint_text || "—"}</div>
              </div>
              <div className="cd-diff-col" style={{ borderLeft: "1px solid var(--gr3)", background: "var(--bk2)" }}>
                <div className="cd-diff-col-hdr cd-diff-col-hdr-pub"><span className="specimen specimen-g">New value · published (current)</span></div>
                <div style={{ padding: 14 }} className="cd-diff-text">{diff.published_text || "—"}</div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", padding: "8px 16px", borderTop: "1px solid var(--gr3)" }}>
              {diff.change_description && <span className="specimen">{diff.change_description}</span>}
              {diff.root_cause && <span className="specimen specimen-b">root cause: {labelOf(ROOT_CAUSE_LABEL, diff.root_cause)}</span>}
            </div>
          </>
        ) : (
          <div style={{ padding: 16, fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)", fontStyle: "italic" }}>
            This citation is not linked to a single claim diff.
          </div>
        )}
      </div>

      {/* Evidence in the citing paper */}
      <div className="cd-panel" style={{ marginBottom: 16 }}>
        <div className="cd-panel-header">
          <span className="cd-panel-label">Evidence in the citing paper</span>
          <span className="specimen">judged by {c.judged_by ?? "—"}</span>
        </div>
        <div style={{ padding: "12px 16px" }}>
          {c.sentence ? (
            <div style={{ padding: "10px 12px", background: "var(--bk3)", borderLeft: `2px solid ${c.sentence_verified ? "var(--grn)" : "var(--rd)"}`, marginBottom: 10 }}>
              <div style={{ fontSize: 14, fontWeight: 300, color: "var(--wh2)", lineHeight: 1.7, marginBottom: 6 }}>&ldquo;{c.sentence}&rdquo;</div>
              <VerifiedMark
                verified={c.sentence_verified}
                label={c.sentence_verified ? "sentence found verbatim in the paper" : c.sentence_verified === false ? "sentence NOT found verbatim in the paper" : "not checked"}
              />
            </div>
          ) : (
            <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)", fontStyle: "italic", marginBottom: 10 }}>No citing sentence recorded.</div>
          )}
          {c.citation_context && !c.sentence && (
            <div style={{ fontSize: 13, color: "var(--gr)", lineHeight: 1.6, marginBottom: 10 }}>{c.citation_context}</div>
          )}
          {c.reason && <Row k="machine reason">{c.reason}</Row>}
          {c.relayed_by && <Row k="relayed by">{c.relayed_by}</Row>}
          {c.found_via && <Row k="found via">{labelOf(FOUND_VIA_LABEL, c.found_via)}</Row>}
          {c.unclear_rule && (
            <Row k="unclear rule">
              <span className="cd-badge cd-badge-y" style={{ textTransform: "none" }}>{c.unclear_rule}</span>
              {UNCLEAR_RULE_EXPLANATION[c.unclear_rule] ?? ""}
            </Row>
          )}
          {flags.length > 0 && (
            <Row k="pre-screen flags">
              <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {flags.map((f) => (
                  <span key={f}>
                    <span className="cd-badge cd-badge-y" style={{ marginRight: 8 }}>{f}</span>
                    {FLAG_EXPLANATION[f] ?? "unknown flag"}
                  </span>
                ))}
              </span>
            </Row>
          )}
        </div>
      </div>

      {/* Second-pass verification of a superseded verdict */}
      {ver && (
        <div className="cd-panel" style={{ marginBottom: 16 }}>
          <div className="cd-panel-header">
            <span className="cd-panel-label">Verification of the &ldquo;superseded&rdquo; verdict</span>
            <span className="specimen">verdict: {ver.verdict ?? "none returned"}</span>
          </div>
          <div style={{ padding: "8px 16px 12px" }}>
            {CONDITIONS.map(([k, text]) => (
              <div key={k} style={{ display: "flex", gap: 10, alignItems: "baseline", padding: "5px 0", borderBottom: "1px solid var(--gr3)" }}>
                <VerifiedMark verified={ver[k] ?? null} label={ver[k] === true ? "holds" : ver[k] === false ? "fails" : "n/a"} />
                <span style={{ fontSize: 13, color: "var(--wh2)" }}>{text}</span>
              </div>
            ))}
            {ver.reason && <div style={{ fontSize: 13, color: "var(--gr)", marginTop: 8, lineHeight: 1.6 }}>{ver.reason}</div>}
          </div>
        </div>
      )}

      {/* What approving means */}
      <div style={{ marginBottom: 12, padding: "10px 14px", border: "1px solid var(--gr3)", background: "var(--bk2)", fontSize: 13, lineHeight: 1.7, color: "var(--gr)" }}>
        <strong style={{ color: "var(--wh2)" }}>Superseded</strong> and <strong style={{ color: "var(--wh2)" }}>indirect</strong> citations
        are notified automatically when the citation analysis finishes; there is no approval step. Notices go only to the
        project test inbox, never to real authors. Review here confirms or corrects the verdict for evaluation; rejecting a
        citation stops any further notice for it.
      </div>

      <ReviewForm
        kind="citations"
        id={c.affected_citation_id}
        currentStatus={c.review_status}
        currentCites={c.cites}
        currentRole={c.role}
      />
    </div>
  );
}
