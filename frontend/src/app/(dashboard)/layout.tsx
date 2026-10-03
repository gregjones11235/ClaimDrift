import { Sidebar } from "@/components/ui/sidebar";
import { Topbar } from "@/components/ui/topbar";
import { getStats } from "@/lib/api/client";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  // Pending-review count for the nav badge. Best-effort: a BFF hiccup must not
  // take down every dashboard page, so on failure the badge is simply hidden.
  let reviewPending: number | null = null;
  try {
    reviewPending = (await getStats()).review_pending_total ?? null;
  } catch {
    reviewPending = null;
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--bk)", position: "relative" }}>
      {/* Lab grid background */}
      <div className="lab-grid-bg" />

      <Sidebar reviewPending={reviewPending} />

      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: "100vh", position: "relative", zIndex: 2, overflow: "hidden" }}>
        <Topbar />
        <main style={{ flex: 1, padding: "22px 24px", overflowY: "auto", overflowX: "hidden" }}>
          {children}
        </main>
      </div>
    </div>
  );
}
