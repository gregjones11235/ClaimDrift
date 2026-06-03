import { Sidebar } from "@/components/ui/sidebar";
import { Topbar } from "@/components/ui/topbar";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="min-h-full flex bg-white text-black selection:bg-black selection:text-white w-full">
      <Sidebar />
      <div className="flex-1 flex flex-col min-h-screen max-w-full overflow-hidden border-l border-black">
        <Topbar />
        <main className="flex-1 p-8 overflow-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
