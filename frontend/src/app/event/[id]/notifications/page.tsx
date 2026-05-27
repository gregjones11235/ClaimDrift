import { getNotifications } from "@/lib/api/client";
import { ArrowLeft, Mail } from "lucide-react";
import Link from "next/link";

export default async function NotificationsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { items: notifications } = await getNotifications(id);

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
            {notifications.length} emails drafted · sent_at: null in demo
          </div>
        </div>
        <div className="bg-[#FFF4E6] text-[#E8590C] border border-[#E8590C] px-2 py-0.5 text-[12px] font-medium font-sans">
          {notifications.length} drafted
        </div>
      </div>

      <div className="flex flex-col gap-3 mb-8">
        {notifications.length === 0 ? (
          <div className="flex items-center justify-center h-48 border border-black rounded-none bg-white text-[13px] text-[#666] italic font-sans">
            No notifications have been drafted for this event.
          </div>
        ) : (
          notifications.map((n, i) => {
            const name = n.recipient_email.split('@')[0].split('+')[1] || n.recipient_email.split('@')[0];
            const capitalizedName = name.charAt(0).toUpperCase() + name.slice(1) + " Doe";
            
            return (
              <div key={i} className="border border-black border-l-4 border-l-[#E8590C] p-4 bg-white flex justify-between items-start">
                <div>
                  <div className="text-[14px] font-sans text-black mb-1">
                    <span className="font-bold">{capitalizedName}</span> · {n.recipient_email}
                  </div>
                  <div className="text-[13px] font-sans text-[#666] mb-1">
                    Citation ID: {n.affected_citation_id}
                  </div>
                  <div className="text-[12px] font-mono text-[#666]">
                    subject: "{n.subject}"
                  </div>
                </div>
                <div className="bg-[#FFF4E6] text-[#E8590C] px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide">
                  drafted
                </div>
              </div>
            );
          })
        )}
      </div>

      {notifications.length > 0 && (
        <div className="border border-[#1971C2] bg-[#E7F5FF] p-5 rounded-none">
          <div className="text-[13px] font-medium text-[#1971C2] mb-4 flex items-center gap-2">
            <Mail className="w-4 h-4" /> Email preview — {(notifications[0].recipient_email.split('@')[0].split('+')[1] || notifications[0].recipient_email.split('@')[0]).charAt(0).toUpperCase() + (notifications[0].recipient_email.split('@')[0].split('+')[1] || notifications[0].recipient_email.split('@')[0]).slice(1)} Doe (real notifier agent output)
          </div>
          <div className="text-[12px] font-mono text-[#666] mb-1">
            recipient_email: <span className="text-black">{notifications[0].recipient_email}</span>
          </div>
          <div className="text-[12px] font-mono text-[#666] mb-4">
            status: <span className="text-black">{notifications[0].status}</span> · sent_at: <span className="text-black">null</span>
          </div>
          <div className="text-[13px] font-sans text-[#1971C2] leading-relaxed whitespace-pre-wrap">
            {notifications[0].body.split(/(45%|12%)/).map((part, index) => {
              if (part === '45%') return <span key={index} className="text-[#E03131]">45%</span>;
              if (part === '12%') return <span key={index} className="text-[#2F9E44]">12%</span>;
              return part;
            })}
          </div>
        </div>
      )}
    </div>
  );
}
