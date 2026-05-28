import { getAffectedCitations, getDriftEvent, getNotifications } from "@/lib/api/client";
import { ArrowLeft, Mail } from "lucide-react";
import Link from "next/link";
import dayjs from "dayjs";
import { NotificationStatus } from "@/types/claimdrift";

// Map notification_log.status → presentation. Source of truth is the dispatcher
// (apps/dispatcher/main.py:send_and_update). Keep this table in sync with the
// status enum in claimdrift.ts.
const STATUS_STYLE: Record<NotificationStatus, { bg: string; fg: string; border: string; label: string }> = {
  sent:    { bg: "#E6F4F0", fg: "#0D7A5F", border: "#0D7A5F", label: "sent" },
  drafted: { bg: "#FFF4E6", fg: "#E8590C", border: "#E8590C", label: "drafted" },
  skipped: { bg: "#F5F5F5", fg: "#666666", border: "#666666", label: "skipped" },
  bounced: { bg: "#FFF5F5", fg: "#C92A2A", border: "#C92A2A", label: "bounced" },
  failed:  { bg: "#FFF5F5", fg: "#C92A2A", border: "#C92A2A", label: "failed" },
};

function statusStyle(s: NotificationStatus) {
  return STATUS_STYLE[s] ?? STATUS_STYLE.drafted;
}

// Highlight every numerical value mentioned in any of the drift event's
// numerical_delta blocks. We deliberately do not parse the email body to
// guess what's noteworthy; we trust drift_analyzer's structured output.
function highlightDeltas(body: string, values: { value: number; color: string }[]) {
  if (values.length === 0) return body;
  // Build one regex matching any of the values, treating each value as either
  // the bare number or the value followed by `%`. We use word boundaries so
  // "5" doesn't match "50".
  const escaped = values
    .map((v) => v.value.toString().replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  const re = new RegExp(`(\\b(?:${escaped})\\b%?)`, "g");
  const parts = body.split(re);
  return parts.map((part, i) => {
    const v = values.find(
      (v) =>
        part === v.value.toString() ||
        part === `${v.value}%`
    );
    if (v) {
      return (
        <span key={i} style={{ color: v.color, fontWeight: 600 }}>
          {part}
        </span>
      );
    }
    return part;
  });
}

export default async function NotificationsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [{ items: notifications }, { items: citations }, event] = await Promise.all([
    getNotifications(id),
    getAffectedCitations(id),
    getDriftEvent(id),
  ]);

  // Index citation_finder authors by affected_citation_id so each notification
  // can recover the recipient's real name (notification_log itself only carries
  // recipient_email — names live upstream on the affected_citations row).
  const authorByAcId = new Map<string, string | null>();
  for (const c of citations) {
    const firstName = c.citing_paper_authors?.[0]?.name ?? null;
    authorByAcId.set(c.affected_citation_id, firstName);
  }

  // Build the numerical highlight set from drift_event.claim_diffs[].numerical_delta.
  // Red = the value that shrank, green = the value that grew. Comparison direction
  // is taken from absolute_delta sign on the published value relative to preprint.
  const deltaValues: { value: number; color: string }[] = [];
  for (const diff of event.claim_diffs ?? []) {
    const d = diff.numerical_delta;
    if (!d) continue;
    const decreased = d.published_value < d.preprint_value;
    deltaValues.push({ value: d.preprint_value, color: decreased ? "#C92A2A" : "#2F9E44" });
    deltaValues.push({ value: d.published_value, color: decreased ? "#2F9E44" : "#C92A2A" });
  }

  const sentCount = notifications.filter((n) => n.status === "sent").length;

  return (
    <div className="max-w-4xl">
      <Link href={`/event/${id}`} className="inline-flex items-center gap-1.5 text-[13px] font-sans text-black hover:underline mb-6 transition-colors">
        <ArrowLeft className="w-3.5 h-3.5" /> Back to Drift Detail
      </Link>

      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-[22px] font-medium font-sans text-black mb-2 flex items-center gap-2">
            <Mail className="w-5 h-5" /> Notification log
          </h1>
          <div className="text-[13px] text-[#666] font-sans">
            {notifications.length} notification{notifications.length === 1 ? "" : "s"}
            {notifications.length > 0 && ` · ${sentCount}/${notifications.length} sent`}
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3 mb-8">
        {notifications.length === 0 ? (
          <div className="flex items-center justify-center h-48 border border-black rounded-none bg-white text-[13px] text-[#666] italic font-sans">
            No notifications have been drafted for this event.
          </div>
        ) : (
          notifications.map((n) => {
            const style = statusStyle(n.status);
            const recipientName = authorByAcId.get(n.affected_citation_id);
            return (
              <div
                key={n.affected_citation_id}
                className="border border-black border-l-4 p-4 bg-white flex justify-between items-start"
                style={{ borderLeftColor: style.border }}
              >
                <div className="min-w-0 flex-1 pr-4">
                  <div className="text-[14px] font-sans text-black mb-1">
                    {recipientName ? (
                      <>
                        <span className="font-bold">{recipientName}</span>
                        <span className="text-[#666]"> · {n.recipient_email}</span>
                      </>
                    ) : (
                      <span className="font-mono text-[13px]">{n.recipient_email}</span>
                    )}
                  </div>
                  <div className="text-[12px] font-mono text-[#666] mb-1 truncate" title={n.affected_citation_id}>
                    affected_citation_id: {n.affected_citation_id}
                  </div>
                  <div className="text-[12px] font-mono text-[#666] truncate" title={n.subject}>
                    subject: &quot;{n.subject}&quot;
                  </div>
                  {n.error_message && (
                    <div className="text-[12px] font-mono text-[#C92A2A] mt-1">
                      error: {n.error_message}
                    </div>
                  )}
                </div>
                <div
                  className="px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide"
                  style={{ backgroundColor: style.bg, color: style.fg }}
                >
                  {style.label}
                </div>
              </div>
            );
          })
        )}
      </div>

      {notifications.length > 0 && (
        <div className="border border-[#1971C2] bg-[#E7F5FF] p-5 rounded-none">
          {(() => {
            const preview = notifications[0];
            const style = statusStyle(preview.status);
            const previewName = authorByAcId.get(preview.affected_citation_id);
            return (
              <>
                <div className="text-[13px] font-medium text-[#1971C2] mb-4 flex items-center gap-2">
                  <Mail className="w-4 h-4" />
                  Email preview{previewName ? ` — ${previewName}` : ""} (real notifier agent output)
                </div>
                <div className="text-[12px] font-mono text-[#666] mb-1">
                  recipient_email: <span className="text-black">{preview.recipient_email}</span>
                </div>
                <div className="text-[12px] font-mono text-[#666] mb-1">
                  subject: <span className="text-black">{preview.subject}</span>
                </div>
                <div className="text-[12px] font-mono text-[#666] mb-4">
                  status: <span style={{ color: style.fg }}>{preview.status}</span>
                  {preview.sent_at && (
                    <>
                      {" · "}sent_at: <span className="text-black">{dayjs(preview.sent_at).format("YYYY-MM-DD HH:mm:ss")}</span>
                    </>
                  )}
                </div>
                <div className="text-[13px] font-sans text-[#1971C2] leading-relaxed whitespace-pre-wrap">
                  {highlightDeltas(preview.body, deltaValues)}
                </div>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}
