import { Sidebar } from "@/components/ui/sidebar";
import { Topbar } from "@/components/ui/topbar";
import { requireUser } from "@/lib/api/server";

// Customer area (every page needs a login). The operator review queue lives under /ops (app/(ops)); only an admin's
// sidebar links to it.
export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const user = await requireUser();
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--bk)", position: "relative" }}>
      {/* Lab grid background */}
      <div className="lab-grid-bg" />

      <Sidebar variant="customer" user={user} />

      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: "100vh", position: "relative", zIndex: 2, overflow: "hidden" }}>
        <Topbar />
        <main style={{ flex: 1, padding: "22px 24px", overflowY: "auto", overflowX: "hidden" }}>
          {children}
        </main>
      </div>
    </div>
  );
}
