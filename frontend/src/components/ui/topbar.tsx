"use client";

import { usePathname } from "next/navigation";
import { useSseStore } from "@/lib/store/sse";
import { useEffect, useState } from "react";

export function Topbar() {
  const pathname = usePathname();
  const isListening = useSseStore((s) => s.isListening);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  let title = "Dashboard";
  let sub = "";
  if (pathname?.startsWith("/event/") && pathname.includes("/citations")) {
    title = "Citations"; sub = "blast radius · OpenAlex";
  } else if (pathname?.startsWith("/event/") && pathname.includes("/notifications")) {
    title = "Notification Log"; sub = "notifier agent · Gmail";
  } else if (pathname?.startsWith("/event/")) {
    title = "Drift Detail"; sub = "drift_analyzer · Gemini 2.5 Pro";
  } else if (pathname === "/live") {
    title = "Live Stream"; sub = "agent_events · SSE";
  } else if (pathname === "/patterns") {
    title = "Pattern Memory"; sub = "drift_patterns · ELSER";
  } else if (pathname === "/citations") {
    title = "Citations"; sub = "blast radius · OpenAlex";
  } else if (pathname === "/notifications") {
    title = "Notification Log"; sub = "notifier agent";
  } else if (pathname === "/dashboard") {
    title = "Dashboard"; sub = "ClaimDrift monitoring";
  }

  return (
    <div style={{
      height: 48,
      flexShrink: 0,
      borderBottom: "1px solid var(--gr3)",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "0 24px",
      background: "rgba(10,10,10,0.9)",
      backdropFilter: "blur(8px)",
      position: "sticky",
      top: 0,
      zIndex: 40,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div style={{ fontFamily: "var(--display)", fontSize: 13, fontWeight: 800, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--wh)" }}>
          {title}
        </div>
        {sub && (
          <span className="specimen" style={{ color: "var(--gr2)" }}>{sub}</span>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        {/* Live indicator — only shown on /live */}
        {mounted && pathname === "/live" && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 10px", border: `1px solid ${isListening ? "var(--grn)" : "var(--gr3)"}` }}>
            <div style={{
              width: 6, height: 6, borderRadius: "50%",
              background: isListening ? "var(--grn)" : "var(--gr2)",
              animation: isListening ? "pulse-dot 1.5s ease-out infinite" : "none",
            }} />
            <span className="specimen" style={{ color: isListening ? "var(--grn)" : "var(--gr2)" }}>
              {isListening ? "Live" : "Idle"}
            </span>
          </div>
        )}

        {/* Global monitoring status */}
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--grn)" }} />
          <span className="specimen specimen-g">2 active</span>
        </div>
      </div>
    </div>
  );
}
