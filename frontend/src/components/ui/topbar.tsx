"use client";

import { usePathname } from "next/navigation";
import { useSseStore } from "@/lib/store/sse";
import { useSyncExternalStore } from "react";

const noopSubscribe = () => () => {};

export function Topbar() {
  const pathname = usePathname();
  const isListening = useSseStore((s) => s.isListening);
  // true on the client, false during SSR — avoids a hydration mismatch for the
  // SSE indicator without a setState-in-effect.
  const mounted = useSyncExternalStore(noopSubscribe, () => true, () => false);

  let title = "Dashboard";
  let sub = "";
  if (pathname?.startsWith("/event/") && pathname.includes("/citations")) {
    title = "Citations"; sub = "citation analysis · open full-text papers";
  } else if (pathname?.startsWith("/event/") && pathname.includes("/notifications")) {
    title = "Notification Log"; sub = "notifier · test inbox only";
  } else if (pathname?.startsWith("/event/")) {
    title = "Drift Detail"; sub = "drift_analyzer · two severity scales";
  } else if (pathname?.startsWith("/ops/review/events/")) {
    title = "Review · Drift Event"; sub = "operator · quality control";
  } else if (pathname?.startsWith("/ops/review/citations/")) {
    title = "Review · Citation"; sub = "operator · quality control";
  } else if (pathname === "/ops/review") {
    title = "Review Queue"; sub = "operator · quality control";
  } else if (pathname === "/selfcheck") {
    title = "Author Self-check"; sub = "before submission · after publication";
  } else if (pathname?.startsWith("/playground")) {
    title = "Playground"; sub = "5-agent orchestration";
  } else if (pathname === "/live") {
    title = "Event Stream"; sub = "agent_events · SSE";
  } else if (pathname === "/citations") {
    title = "Citations"; sub = "Europe PMC open full text";
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
        {/* Connection indicator — only shown on /live. Reflects whether the SSE
            EventSource is currently open (Streaming) or not (Idle). */}
        {mounted && pathname === "/live" && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 10px", border: `1px solid ${isListening ? "var(--grn)" : "var(--gr3)"}` }}>
            <div style={{
              width: 6, height: 6, borderRadius: "50%",
              background: isListening ? "var(--grn)" : "var(--gr2)",
              animation: isListening ? "pulse-dot 1.5s ease-out infinite" : "none",
            }} />
            <span className="specimen" style={{ color: isListening ? "var(--grn)" : "var(--gr2)" }}>
              {isListening ? "Streaming" : "Idle"}
            </span>
          </div>
        )}

        {/* Global online indicator. Deliberately carries NO count: there was a
            hard-coded "2 active" here that mapped to no real data. */}
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--grn)" }} />
          <span className="specimen specimen-g">Active</span>
        </div>
      </div>
    </div>
  );
}
