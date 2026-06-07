"use client";

import { useState } from "react";
import { NotificationLog, NotificationStatus } from "@/types/claimdrift";

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
              {notif.recipient_email}
            </div>
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
              {[["To", notif.recipient_email], ["Subject", notif.subject]].map(([k, v]) => (
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
              {notif.error_message && <span className="specimen specimen-r">{notif.error_message}</span>}
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

      <div className="cd-panel">
        <div className="cd-panel-header">
          <span className="cd-panel-label">notification_log — dispatch status</span>
          <span className="specimen">Gmail OAuth · notifier agent · Gemini 2.5 Flash</span>
        </div>
        <div>
          {notifications.map((n) => <NotifCard key={n.affected_citation_id} notif={n} />)}
        </div>
        <div style={{ padding: "10px 16px", borderTop: "1px solid var(--gr3)", display: "flex", justifyContent: "space-between" }}>
          <span className="specimen">{notifications.length} total · {sent} sent · {drafted} drafted · {skipped} skipped</span>
          <span className="specimen specimen-g">claimdriftnotifier@gmail.com</span>
        </div>
      </div>
    </>
  );
}
