import { Sidebar } from "@/components/ui/sidebar";
import { Topbar } from "@/components/ui/topbar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--bk)", position: "relative" }}>
      {/* Lab grid background */}
      <div className="lab-grid-bg" />

      <Sidebar />

      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: "100vh", position: "relative", zIndex: 2, overflow: "hidden" }}>
        <Topbar />
        <main style={{ flex: 1, padding: "22px 24px", overflowY: "auto" }}>
          {children}
        </main>
      </div>
    </div>
  );
}
