import Link from "next/link";
import { bffErrorMessage, getReviewEvent } from "@/lib/api/client";
import { ReviewEventDetail } from "@/types/claimdrift";
import { BffNotice } from "@/components/features/BffNotice";
import { CitesBadge, FlagBadges, ReasonBadges, ReviewStatusBadge, VerifiedMark } from "@/components/features/Badges";
import { SeverityPanels } from "@/components/features/SeverityPanels";
import { EventFactsPanel } from "@/components/features/EventFactsPanel";
import { ClaimDiffViewer } from "@/components/features/ClaimDiffViewer";
import { CitationRunsPanel } from "@/components/features/CitationRunsPanel";
import { ReviewForm } from "@/components/features/ReviewForm";
import { ROLE_LABEL, labelOf, shortDate } from "@/lib/labels";

const backLink: React.CSSProperties = {
  display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--mono)", fontSize: 12,
  letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--gr)", textDecoration: "none", marginBottom: 16,
};

export default async function ReviewEventPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let data: ReviewEventDetail | null = null;
  let error: string | null = null;
  try {
    data = await getReviewEvent(id);
  } catch (e) {
    error = bffErrorMessage(e);
  }

  if (!data) {
    return (
      <div>
        <Link href="/review" style={backLink}>← Review queue</Link>
        <BffNotice message={error ?? "Event not found."} />
      </div>
    );
  }

  const { event, citation_runs: runs, citations } = data;

  return (
    <div>
      <Link href="/review" style={backLink}>← Review queue</Link>

      {/* Header */}
      <div className="cd-panel" style={{ marginBottom: 16 }}>
        <div className="cd-panel-header">
          <span className="cd-panel-label">Drift event review</span>
          <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <ReviewStatusBadge status={event.review_status} />
          </span>
        </div>
        <div style={{ padding: "14px 16px" }}>
          <div style={{ fontSize: 17, color: "var(--wh)", lineHeight: 1.45, marginBottom: 6 }}>
            {event.preprint_title || event.paper_id || event.event_id}
          </div>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 8 }}>
            {event.first_author && <span className="specimen">{event.first_author} et al.</span>}
            {event.paper_id && <span className="specimen">{event.paper_id}</span>}
            <span className="specimen">detected {shortDate(event.detected_at)}</span>
          </div>
          <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--gr)" }}>{event.preprint_doi}</div>
          <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--bl)", marginBottom: 10 }}>↳ {event.published_doi}</div>
          {(event.review_reasons?.length ?? 0) > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              <span className="specimen">in review because:</span>
              <ReasonBadges reasons={event.review_reasons} />
            </div>
          )}
          {event.drift_summary && (
            <div style={{ marginTop: 12, fontSize: 14, fontWeight: 300, color: "var(--wh2)", lineHeight: 1.7 }}>
              &ldquo;{event.drift_summary}&rdquo;
            </div>
          )}
          <div style={{ marginTop: 10 }}>
            <Link href={`/event/${event.event_id}`} className="cd-btn" style={{ padding: "6px 12px", fontSize: 11 }}>Open event view →</Link>
          </div>
        </div>
      </div>

      <ReviewForm
        kind="events"
        id={event.event_id}
        currentStatus={event.review_status}
        currentAbstractClass={event.abstract_severity?.class}
        currentFulltextTier={event.fulltext_severity?.tier}
        diffs={(event.claim_diffs ?? []).map((d, idx) => ({
          idx,
          label: d.change_description || d.preprint_text || d.published_text || "",
          root_cause: d.root_cause,
          severity_tier: d.severity_tier,
        }))}
      />

      <SeverityPanels
        abstractSeverity={event.abstract_severity}
        fulltextSeverity={event.fulltext_severity}
        legacyMateriality={event.materiality_score}
      />

      <EventFactsPanel event={event} versionCompared={event.preprint_version_compared} />

      {/* Claim diffs with evidence + verbatim check */}
      {(event.claim_diffs ?? []).map((diff, idx) => (
        <div key={idx} style={{ marginBottom: 16 }}>
          <ClaimDiffViewer diff={diff} index={idx} provenance={event.provenance} />
        </div>
      ))}

      <CitationRunsPanel analysis={event.citation_analysis} runs={runs} />

      {/* Citations for this event */}
      <div className="cd-panel">
        <div className="cd-panel-header">
          <span className="cd-panel-label">Citing papers judged</span>
          <span className="specimen">{citations.length} paper(s) · within open full-text papers</span>
        </div>
        {citations.length === 0 ? (
          <div style={{ padding: 16, fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)", fontStyle: "italic" }}>No citation verdicts for this event.</div>
        ) : (
          citations.map((c) => (
            <Link key={c.id} href={`/review/citations/${encodeURIComponent(c.id)}`} className="hover:bg-[rgba(245,197,24,0.03)]"
              style={{ display: "block", padding: "10px 16px", borderBottom: "1px solid var(--gr3)", textDecoration: "none" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontSize: 13, color: "var(--wh2)", flex: 1, minWidth: 240 }}>{c.title || c.work_id}</span>
                <CitesBadge cites={c.cites} />
                {c.role && <span className="cd-badge">{labelOf(ROLE_LABEL, c.role)}</span>}
                <FlagBadges flags={c.flags} />
                {c.sentence && <VerifiedMark verified={c.sentence_verified} label={c.sentence_verified ? "sentence verbatim" : c.sentence_verified === false ? "sentence not found" : "sentence unchecked"} />}
                <ReviewStatusBadge status={c.review_status} />
              </div>
            </Link>
          ))
        )}
      </div>
    </div>
  );
}
