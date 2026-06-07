import { getNotifications } from "@/lib/api/client";
import { NotificationLogList } from "@/components/features/NotificationLogList";
import Link from "next/link";

export default async function NotificationsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { items: notifications } = await getNotifications(id);

  return (
    <div style={{ width: "100%" }}>
      <Link href={`/event/${id}`} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--mono)", fontSize: 9, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--gr)", textDecoration: "none", marginBottom: 16 }}>
        ← Drift Detail
      </Link>
      <NotificationLogList notifications={notifications} />
    </div>
  );
}
