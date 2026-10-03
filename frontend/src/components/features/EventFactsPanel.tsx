import { DriftEventNewFields } from "@/types/claimdrift";
import { VERIFICATION_STATUS_COLOR, shortDateTime } from "@/lib/labels";
import { ColorBadge, ReasonBadges, ReviewStatusBadge } from "./Badges";

function Fact({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "baseline", padding: "6px 0", borderBottom: "1px solid var(--gr3)" }}>
      <span className="specimen" style={{ width: 150, flexShrink: 0 }}>{k}</span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--wh2)", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        {children}
      </span>
    </div>
  );
}

// How the event was produced and checked: analysis route, versions compared,
// quote verification and human review. Renders nothing for old demo events that
// carry none of these fields.
export function EventFactsPanel({
  event,
  versionCompared,
}: {
  event: DriftEventNewFields;
  versionCompared?: string | null;
}) {
  const route = event.analysis_route;
  const ver = event.verification;
  const withdrawn = event.preprint_withdrawn_versions ?? [];
  const hasAny =
    route || ver || event.text_source || event.review_status || (event.preprint_versions?.length ?? 0) > 0;
  if (!hasAny) return null;

  return (
    <div className="cd-panel" style={{ marginBottom: 16 }}>
      <div className="cd-panel-header">
        <span className="cd-panel-label">Analysis &amp; verification</span>
        <span className="specimen">how this event was produced and checked</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px", padding: "6px 16px 10px" }}>
        <div>
          {route && (
            <Fact k="analysis route">
              <span>{route.route === "stuffed" ? "full text in one call (stuffed)" : "extracted claims (long-document branch)"}</span>
              {route.est_tokens != null && (
                <span style={{ color: "var(--gr2)" }}>
                  ~{route.est_tokens.toLocaleString()} tokens{route.limit != null ? ` / limit ${route.limit.toLocaleString()}` : ""}
                </span>
              )}
              {route.forced && <span className="cd-badge cd-badge-y">forced</span>}
              {route.prompt_version && <span style={{ color: "var(--gr2)" }}>prompt {route.prompt_version}</span>}
            </Fact>
          )}
          {(versionCompared || event.text_source) && (
            <Fact k="compared">
              {versionCompared ?? "?"} → published
              {event.text_source && <span style={{ color: "var(--gr2)" }}>· text source: {event.text_source.toUpperCase()}</span>}
            </Fact>
          )}
          {(event.preprint_versions?.length ?? 0) > 0 && (
            <Fact k="preprint versions">
              {event.preprint_versions!.map((v) => (
                <span key={v} className={`cd-badge ${withdrawn.includes(v) ? "cd-badge-r" : ""}`}>
                  {v}{withdrawn.includes(v) ? " · withdrawn" : ""}
                </span>
              ))}
            </Fact>
          )}
          {withdrawn.length > 0 && (event.preprint_versions?.length ?? 0) === 0 && (
            <Fact k="withdrawn versions">
              {withdrawn.map((v) => <span key={v} className="cd-badge cd-badge-r">{v}</span>)}
            </Fact>
          )}
        </div>
        <div>
          {ver && (
            <Fact k="quote verification">
              <ColorBadge color={VERIFICATION_STATUS_COLOR[ver.status] ?? "var(--gr)"}>{ver.status}</ColorBadge>
              <span>
                {ver.quotes_verified}/{ver.quotes_total} quotes found verbatim
              </span>
              {ver.method && <span style={{ color: "var(--gr2)" }}>via {ver.method}</span>}
            </Fact>
          )}
          {event.review_status && (
            <Fact k="human review">
              <ReviewStatusBadge status={event.review_status} />
              {event.reviewer && (
                <span style={{ color: "var(--gr2)" }}>
                  by {event.reviewer} · {shortDateTime(event.reviewed_at)}
                </span>
              )}
            </Fact>
          )}
          {(event.review_reasons?.length ?? 0) > 0 && (
            <Fact k="review reasons">
              <ReasonBadges reasons={event.review_reasons} />
            </Fact>
          )}
          {event.review_note && <Fact k="review note">{event.review_note}</Fact>}
        </div>
      </div>
    </div>
  );
}
