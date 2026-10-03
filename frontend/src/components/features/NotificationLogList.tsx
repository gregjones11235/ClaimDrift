"use client";

import { useState } from "react";
import { NotificationLog, NotificationStatus } from "@/types/claimdrift";
import { TEST_INBOX } from "@/lib/labels";

const STATUS_COLOR: Record<NotificationStatus, string> = {
  sent:    "var(--grn)",
  drafted: "var(--y)",
  skipped: "var(--gr2)",
  bounced: "var(--or)",
  failed:  "var(--rd)",
};
const STATUS_ICON: Record<NotificationStatus, string> = {
  sent: "✓", drafted: "◑", skipped: "—", bounced: "!", failed: "✗",
};

function NotifCard({ notif }: { notif: NotificationLog }) {
  const [open, setOpen] = useState(false);
  const color = STATUS_COLOR[notif.status];

  return (
    <div style={{ borderBottom: "1px solid var(--gr3)" }}>
      <div style={{ padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, cursor: notif.status !== "skipped" ? "pointer" : "default" }}
        onClick={() => notif.status !== "skipped" && setOpen(!open)}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flex: 1, minWidth: 0 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 7.5, letterSpacing: "0.08em", textTransform: "uppercase", padding: "3px 8px", border: `1px solid ${color}`, color, whiteSpace: "nowrap", flexShrink: 0 }}>
            {STATUS_ICON[notif.status]} {notif.status}
          </span>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 12, fontWeight: 500, color: "var(--wh2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {notif.intended_for?.citing_paper_title
                ? <>for: {notif.intended_for.citing_paper_title}</>
                : notif.recipient_email}
            </div>
            {(notif.cites || notif.delivery || notif.citing_work_id) && (
              <div style={{ display: "flex", gap: 8, marginTop: 2, flexWrap: "wrap" }}>
                {notif.cites && <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--y)" }}>{notif.cites}</span>}
                {notif.citing_work_id && <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--gr)" }}>{notif.citing_work_id}</span>}
                {notif.delivery && <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--gr2)" }}>{notif.delivery === "gmail" ? "sent via Gmail" : "draft only"}</span>}
              </div>
            )}
            {notif.sent_at && (
              <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--bl)", marginTop: 1 }}>
                sent_at: {notif.sent_at}
              </div>
            )}
          </div>
        </div>
        {notif.status !== "skipped" && (
          <button style={{ fontFamily: "var(--mono)", fontSize: 8.5, color: "var(--gr2)", background: "none", border: "none", cursor: "pointer", padding: "3px 6px" }}>
            {open ? "▴" : "▾"}
          </button>
        )}
      </div>

      {open && (
        <div style={{ padding: "0 16px 14px" }}>
          <div style={{ border: "1px solid var(--gr3)", background: "var(--bk3)" }}>
            <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--gr3)" }}>
              {([
                ["To", `${notif.recipient_email} (project test inbox)`],
                ...(notif.intended_for
                  ? [["For", [
                      notif.intended_for.citing_paper_title,
                      notif.intended_for.citing_paper_doi,
                      notif.intended_for.authors?.length ? notif.intended_for.authors.join(", ") : null,
                      notif.intended_for.relayed_by ? `relayed by ${notif.intended_for.relayed_by}` : null,
                    ].filter(Boolean).join(" · ")]]
                  : []),
                ...(notif.reviewer
                  ? [["Approved", `${notif.reviewer}${notif.approved_at ? ` · ${notif.approved_at.slice(0, 16).replace("T", " ")}` : ""}`]]
                  : []),
                ["Subject", notif.subject],
              ] as [string, string][]).map(([k, v]) => (
                <div key={k} style={{ display: "flex", gap: 8, alignItems: "baseline", marginBottom: 3 }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 7.5, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--gr2)", width: 44, flexShrink: 0 }}>{k}</span>
                  <span style={{ fontSize: 11, color: k === "Subject" ? "var(--wh)" : "var(--wh2)", fontWeight: k === "Subject" ? 500 : 400 }}>{v}</span>
                </div>
              ))}
            </div>
            <div style={{ padding: 12, fontFamily: "var(--mono)", fontSize: 9.5, color: "var(--gr)", lineHeight: 1.8, whiteSpace: "pre-line" }}>
              {notif.body}
            </div>
            <div style={{ padding: "7px 12px", borderTop: "1px solid var(--gr3)", display: "flex", alignItems: "center", gap: 10, background: notif.status === "sent" ? "rgba(62,207,142,0.04)" : "transparent" }}>
              <div style={{ width: 5, height: 5, borderRadius: "50%", background: color }} />
              <span className="specimen" style={{ color }}>{notif.status === "sent" ? `Delivered · ${notif.sent_at}` : notif.status}</span>
              {/* A delivered email is a success — drop any error_message that lingers
                  from a transient send failure that was retried successfully (Gmail
                  429 / BrokenPipe / SSL EOF). Only surface errors on a non-sent row. */}
              {notif.status !== "sent" && notif.error_message && (
                <span className="specimen specimen-r">{notif.error_message}</span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function NotificationLogList({ notifications }: { notifications: NotificationLog[] }) {
  const sent    = notifications.filter((n) => n.status === "sent").length;
  const drafted = notifications.filter((n) => n.status === "drafted").length;
  const skipped = notifications.filter((n) => n.status === "skipped").length;
  const failed  = notifications.filter((n) => n.status === "failed").length;

  return (
    <>
      <div className="cd-stat-grid" style={{ gridTemplateColumns: "repeat(4,1fr)", marginBottom: 18 }}>
        {[
          { label: "Sent",    val: sent,    color: "var(--grn)" },
          { label: "Drafted", val: drafted, color: "var(--y)"   },
          { label: "Skipped", val: skipped, color: "var(--gr2)" },
          { label: "Failed",  val: failed,  color: "var(--rd)"  },
        ].map(({ label, val, color }, i) => (
          <div key={i} className="cd-stat-cell">
            <style>{`.cd-stat-cell:nth-child(${i+1})::before { background: ${color}; }`}</style>
            <div className="specimen">{label}</div>
            <div className="cd-stat-val" style={{ color }}>{val}</div>
          </div>
        ))}
      </div>

      <div style={{ marginBottom: 16, padding: "10px 14px", border: "1px solid var(--gr3)", background: "var(--bk2)", fontSize: 13, lineHeight: 1.6, color: "var(--gr)" }}>
        <span className="specimen specimen-y">note</span>{" "}
        Every notice goes to the project test inbox <span style={{ fontFamily: "var(--mono)", color: "var(--wh2)" }}>{TEST_INBOX}</span>,
        never to real authors. &ldquo;For&rdquo; shows the citing paper a notice is written about. Only citations judged
        superseded or indirect are notified, and none whose citation or drift event a reviewer rejected.
      </div>

      <div className="cd-panel">
        <div className="cd-panel-header">
          <span className="cd-panel-label">notification_log — dispatch status</span>
          <span className="specimen">Gmail API · notifier agent</span>
        </div>
        <div>
          {notifications.map((n) => <NotifCard key={n.affected_citation_id} notif={n} />)}
        </div>
        <div style={{ padding: "10px 16px", borderTop: "1px solid var(--gr3)", display: "flex", justifyContent: "space-between" }}>
          <span className="specimen">{notifications.length} total · {sent} sent · {drafted} drafted · {skipped} skipped</span>
          <span className="specimen specimen-g">{TEST_INBOX} · test inbox</span>
        </div>
      </div>
    </>
  );
}
