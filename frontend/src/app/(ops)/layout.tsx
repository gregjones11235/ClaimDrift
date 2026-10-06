import { Sidebar } from "@/components/ui/sidebar";
import { Topbar } from "@/components/ui/topbar";
import { redirect } from "next/navigation";
import { getStats, requireUser } from "@/lib/api/server";

// Operator area (/ops/*): the ClaimDrift team's quality control of its own findings. Admin accounts only; a customer
// is sent back to the dashboard. The BFF enforces the same rule on /api/review* (403), which is the real boundary.
export default async function OpsLayout({ children }: { children: React.ReactNode }) {
  const user = await requireUser();
  if (user.role !== "admin") redirect("/dashboard");

  // Pending-review count for the nav badge. Best-effort: on a BFF failure the badge is hidden.
  let reviewPending: number | null = null;
  try {
    reviewPending = (await getStats()).review_pending_total ?? null;
  } catch {
    reviewPending = null;
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--bk)", position: "relative" }}>
      <div className="lab-grid-bg" />
      <Sidebar variant="ops" user={user} reviewPending={reviewPending} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: "100vh", position: "relative", zIndex: 2, overflow: "hidden" }}>
        <Topbar />
        <main style={{ flex: 1, padding: "22px 24px", overflowY: "auto", overflowX: "hidden" }}>
          {children}
        </main>
      </div>
    </div>
  );
}
