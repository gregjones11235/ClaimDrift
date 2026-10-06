import Link from "next/link";
import { bffErrorMessage } from "@/lib/api/client";
import { getReviewQueue } from "@/lib/api/server";
import { ReviewQueue } from "@/types/claimdrift";
import { BffNotice } from "@/components/features/BffNotice";
import { CitesBadge, FlagBadges, ReasonBadges, ReviewStatusBadge, VerifiedMark } from "@/components/features/Badges";
import { SeverityPair } from "@/components/features/SeverityPanels";
import { NOTIFY_PRIORITY_COLOR, ROLE_LABEL, VERIFICATION_STATUS_COLOR, labelOf, shortDate } from "@/lib/labels";

const STATUSES = ["pending", "approved", "rejected", "not_required"] as const;
const KINDS = ["all", "events", "citations"] as const;
const STATUS_TAB_LABEL: Record<string, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  not_required: "Not required",
};

const th: React.CSSProperties = {
  fontFamily: "var(--mono)", fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
  color: "var(--gr2)", padding: "12px 16px", textAlign: "left", fontWeight: 400,
};
const td: React.CSSProperties = { padding: "12px 16px", verticalAlign: "top" };

export default async function ReviewQueuePage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; kind?: string }>;
}) {
  const sp = await searchParams;
  const status = (STATUSES as readonly string[]).includes(sp.status ?? "") ? sp.status! : "pending";
  const kind = (KINDS as readonly string[]).includes(sp.kind ?? "") ? (sp.kind as (typeof KINDS)[number]) : "all";

  let queue: ReviewQueue | null = null;
  let error: string | null = null;
  try {
    queue = await getReviewQueue(kind, status);
  } catch (e) {
    error = bffErrorMessage(e);
  }

  const events = queue?.events ?? [];
  const citations = queue?.citations ?? [];
  const href = (s: string, k: string) => `/ops/review?status=${s}&kind=${k}`;

  return (
    <div>
      <p style={{ fontSize: 13, lineHeight: 1.7, color: "var(--gr)", maxWidth: 820, marginBottom: 16 }}>
        Operator view: drift events and citation verdicts where an automatic check did not pass. Customers see a pending
        item as &ldquo;Provisional&rdquo;, an approved one as &ldquo;Human-confirmed&rdquo;; a rejected item disappears from
        customer views and is never notified. Notices are not held back while an item is pending.
      </p>

      <div className="cd-panel" style={{ marginBottom: 16 }}>
        <div className="cd-filter-row">
          {STATUSES.map((s) => (
            <Link key={s} href={href(s, kind)} className="cd-filter-tab" data-active={s === status ? "true" : "false"} style={{ textDecoration: "none" }}>
              {STATUS_TAB_LABEL[s]}
            </Link>
          ))}
        </div>
        <div className="cd-filter-row" style={{ borderTop: "1px solid var(--gr3)" }}>
          {KINDS.map((k) => (
            <Link key={k} href={href(status, k)} className="cd-filter-tab" data-active={k === kind ? "true" : "false"} style={{ textDecoration: "none", fontSize: 12, padding: "10px 16px" }}>
              {k === "all" ? "Events + citations" : k}
            </Link>
          ))}
        </div>
      </div>

      {error && <BffNotice message={error} />}

      {queue && kind !== "citations" && (
        <div className="cd-panel" style={{ marginBottom: 16 }}>
          <div className="cd-panel-header">
            <span className="cd-panel-label">Drift events — {STATUS_TAB_LABEL[status]}</span>
            <span className="specimen">{events.length} event(s)</span>
          </div>
          {events.length === 0 ? (
            <div style={{ padding: "16px", fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)", fontStyle: "italic" }}>No events.</div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--gr3)" }}>
                  {["paper", "severity", "quotes", "reasons", "status", ""].map((h) => <th key={h} style={th}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {events.map((ev) => (
                  <tr key={ev.id} className="hover:bg-[rgba(245,197,24,0.03)]" style={{ borderBottom: "1px solid var(--gr3)", position: "relative" }}>
                    <td style={{ ...td, maxWidth: 420 }}>
                      <Link href={`/ops/review/events/${ev.event_id}`} style={{ position: "absolute", inset: 0, zIndex: 1 }} aria-label="Open review" />
                      <div style={{ fontSize: 14, color: "var(--wh2)", lineHeight: 1.45, marginBottom: 4 }}>{ev.title || ev.paper_id || ev.event_id}</div>
                      <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr)" }}>{ev.preprint_doi}</div>
                      <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--bl)" }}>↳ {ev.published_doi}</div>
                    </td>
                    <td style={td}>
                      <SeverityPair abstractClass={ev.abstract_class} fulltextTier={ev.fulltext_tier} />
                    </td>
                    <td style={{ ...td, fontFamily: "var(--mono)", fontSize: 12 }}>
                      {ev.verification ? (
                        <span style={{ color: VERIFICATION_STATUS_COLOR[ev.verification.status] ?? "var(--gr)" }}>
                          {ev.verification.quotes_verified}/{ev.verification.quotes_total} {ev.verification.status}
                        </span>
                      ) : <span style={{ color: "var(--gr2)" }}>—</span>}
                    </td>
                    <td style={td}>
                      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", maxWidth: 320 }}>
                        <ReasonBadges reasons={ev.review_reasons} />
                      </div>
                    </td>
                    <td style={td}>
                      <ReviewStatusBadge status={ev.review_status} />
                      {ev.reviewer && <div className="specimen" style={{ marginTop: 4 }}>{ev.reviewer} · {shortDate(ev.reviewed_at)}</div>}
                      <div className="specimen" style={{ marginTop: 4, color: "var(--gr2)" }}>detected {shortDate(ev.detected_at)}</div>
                    </td>
                    <td style={{ ...td, fontFamily: "var(--mono)", fontSize: 12, color: "var(--gr2)" }}>Review →</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {queue && kind !== "events" && (
        <div className="cd-panel">
          <div className="cd-panel-header">
            <span className="cd-panel-label">Citation verdicts — {STATUS_TAB_LABEL[status]}</span>
            <span className="specimen">{citations.length} citation(s) · high notification priority first</span>
          </div>
          {citations.length === 0 ? (
            <div style={{ padding: "16px", fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)", fontStyle: "italic" }}>No citations.</div>
          ) : (
            <div>
              {citations.map((c) => (
                <Link
                  key={c.id}
                  href={`/ops/review/citations/${encodeURIComponent(c.id)}`}
                  className="hover:bg-[rgba(245,197,24,0.03)]"
                  style={{ display: "block", padding: "12px 16px", borderBottom: "1px solid var(--gr3)", textDecoration: "none", borderLeft: `3px solid ${NOTIFY_PRIORITY_COLOR[c.notify_priority ?? ""] ?? "var(--gr3)"}` }}
                >
                  <div style={{ display: "flex", gap: 10, alignItems: "flex-start", justifyContent: "space-between", marginBottom: 6 }}>
                    <div style={{ fontSize: 14, color: "var(--wh2)", lineHeight: 1.45, flex: 1 }}>
                      {c.title || c.work_id || c.id}
                      {c.work_id && <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--bl)", marginLeft: 8 }}>{c.work_id}</span>}
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                      <CitesBadge cites={c.cites} />
                      {c.role && <span className="cd-badge">{labelOf(ROLE_LABEL, c.role)}</span>}
                      <ReviewStatusBadge status={c.review_status} />
                    </div>
                  </div>
                  {c.sentence && (
                    <div style={{ fontSize: 13, fontWeight: 300, color: "var(--gr)", lineHeight: 1.6, padding: "6px 10px", background: "var(--bk3)", borderLeft: "2px solid var(--gr3)", marginBottom: 6 }}>
                      &ldquo;{c.sentence}&rdquo;
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    {c.sentence && <VerifiedMark verified={c.sentence_verified} />}
                    <FlagBadges flags={c.flags} />
                    <ReasonBadges reasons={c.review_reasons} />
                    {c.notify_priority && <span className="specimen" style={{ color: NOTIFY_PRIORITY_COLOR[c.notify_priority] }}>priority {c.notify_priority}</span>}
                    {c.reviewer && <span className="specimen">{c.reviewer} · {shortDate(c.reviewed_at)}</span>}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
