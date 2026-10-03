import { getDriftEvent, getAffectedCitations, getNotifications, getCitationRuns, bffErrorMessage } from "@/lib/api/client";
import { CitationRun } from "@/types/claimdrift";
import { ClaimDiffViewer } from "@/components/features/ClaimDiffViewer";
import { NumericalDeltaCard } from "@/components/features/NumericalDeltaCard";
import { SeverityPanels } from "@/components/features/SeverityPanels";
import { EventFactsPanel } from "@/components/features/EventFactsPanel";
import { CitationRunsPanel } from "@/components/features/CitationRunsPanel";
import { ReviewStatusBadge } from "@/components/features/Badges";
import Link from "next/link";
import dayjs from "dayjs";

export default async function DriftDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [event, citationsResp, notificationsResp] = await Promise.all([
    getDriftEvent(id),
    getAffectedCitations(id),
    getNotifications(id),
  ]);

  // Citation-analysis runs only exist for events from the redesigned pipeline
  // and need the local ES on the BFF; old demo events simply show none.
  let runs: CitationRun[] | null = null;
  let runsError: string | null = null;
  try {
    runs = (await getCitationRuns(id)).items;
  } catch (e) {
    runsError = bffErrorMessage(e);
  }

  const diffs = event.claim_diffs ?? [];
  const diffTypes = diffs.map((d) => d.diff_type);
  const isNewEvent = Boolean(event.fulltext_severity || event.abstract_severity || event.analysis_route);

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

      {/* Title (new events carry preprint_title / first_author / paper_id) */}
      {(event.preprint_title || event.first_author || event.paper_id) && (
        <div style={{ marginBottom: 12 }}>
          {event.preprint_title && (
            <div style={{ fontSize: 18, color: "var(--wh)", lineHeight: 1.45, marginBottom: 4 }}>{event.preprint_title}</div>
          )}
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
            {event.first_author && <span className="specimen">{event.first_author} et al.</span>}
            {event.paper_id && <span className="specimen">{event.paper_id}</span>}
            <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--gr)" }}>{event.preprint_doi}</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--bl)" }}>↳ {event.published_doi}</span>
          </div>
        </div>
      )}

      {/* Meta strip */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
        {diffTypes[0] && <span className={`cd-badge cd-badge-r`}>{diffTypes[0]}</span>}
        <span className="cd-badge">{event.preprint_version_compared ?? "preprint"} → published</span>
        <ReviewStatusBadge status={event.review_status} />
        {event.review_status && (
          <Link href={`/review/events/${event.event_id}`} className="cd-badge cd-badge-b" style={{ textDecoration: "none" }}>
            review →
          </Link>
        )}
        <span className="specimen" style={{ color: "var(--gr2)", marginLeft: "auto" }}>
          detected: {dayjs(event.detected_at).format("YYYY-MM-DD")}
        </span>
      </div>

      {/* Drift summary */}
      <div className="cd-panel" style={{ marginBottom: 16 }}>
        <div className="cd-panel-header">
          <span className="cd-panel-label">drift_summary</span>
          <span className="specimen">drift_analyzer</span>
        </div>
        <div style={{ padding: "14px 16px", fontSize: 15, fontWeight: 300, color: "var(--wh2)", lineHeight: 1.75 }}>
          {event.drift_summary ? <>&ldquo;{event.drift_summary}&rdquo;</> : <span style={{ color: "var(--gr2)", fontStyle: "italic" }}>No drift summary.</span>}
        </div>
      </div>

      {/* Two severities, shown separately */}
      <SeverityPanels
        abstractSeverity={event.abstract_severity}
        fulltextSeverity={event.fulltext_severity}
        legacyMateriality={event.materiality_score}
      />

      {/* Route, versions, quote verification, review */}
      <EventFactsPanel event={event} versionCompared={isNewEvent ? event.preprint_version_compared : null} />

      {/* Numerical delta + diffs */}
      {diffs.map((diff, idx) => (
        <div key={idx} style={{ marginBottom: 16 }}>
          {diff.numerical_delta && <NumericalDeltaCard delta={diff.numerical_delta} />}
          <ClaimDiffViewer diff={diff} index={idx} provenance={event.provenance} />
        </div>
      ))}

      {/* Citation-analysis status + coverage */}
      <CitationRunsPanel
        analysis={event.citation_analysis}
        runs={runs}
        runsError={event.citation_analysis ? runsError : null}
      />

      {/* Action bar */}
      <div style={{ display: "flex", gap: 8, marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--gr3)" }}>
        <Link href={`/event/${id}/citations`} className="cd-btn cd-btn-blue">
          Citations ({citationsResp.count}) →
        </Link>
        <Link href={`/event/${id}/notifications`} className="cd-btn cd-btn-green">
          Notifications ({notificationsResp.count}) →
        </Link>
      </div>
    </div>
  );
}
