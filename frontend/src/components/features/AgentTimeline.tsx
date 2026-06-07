"use client";

import { SseEvent } from "@/types/claimdrift";
import { useEffect, useRef, useState } from "react";

const AGENT_COLORS: Record<string, string> = {
  claim_extractor:   "var(--y)",
  drift_analyzer:    "var(--rd)",
  citation_finder:   "var(--bl)",
  notifier:          "var(--grn)",
  memory_synthesizer:"var(--pu)",
};

export function AgentTimeline({ events }: { events: SseEvent[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [startTime, setStartTime] = useState<number | null>(null);

  useEffect(() => {
    if (events.length > 0 && startTime === null) {
      const ts = new Date(events[0].timestamp).getTime();
      if (!isNaN(ts)) setStartTime(ts);
    }
  }, [events, startTime]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [events]);

  if (events.length === 0) return null;

  const dataEvents = events.filter((e) => e.event_type !== "heartbeat");

  return (
    <div ref={scrollRef} style={{ maxHeight: 480, overflowY: "auto", padding: "14px 16px", display: "flex", flexDirection: "column", gap: 0 }}>
      {events.map((event, i) => {
        const timeDiff = startTime ? ((new Date(event.timestamp).getTime() - startTime) / 1000).toFixed(1) : "0.0";
        const isLast = i === events.length - 1;
        const agentColor = event.agent_id ? (AGENT_COLORS[event.agent_id] ?? "var(--gr)") : "var(--gr2)";
        const isPattern = event.event_type === "agent.pattern_retrieved";
        const isHeartbeat = event.event_type === "heartbeat";
        const payload = event.payload as Record<string, unknown>;

        return (
          <div key={i} className={`cd-tl-event${isPattern ? " cd-tl-pattern" : ""}`} style={{ opacity: isHeartbeat ? 0.35 : 1 }}>
            <div className="cd-tl-spine">
              <div className="cd-tl-dot" style={{
                background: isPattern ? "var(--pu)" : isHeartbeat ? "var(--gr3)" : agentColor,
                boxShadow: isPattern ? "0 0 8px rgba(167,139,250,0.5)" : "none",
              }} />
              {!isLast && <div className="cd-tl-line" style={{ background: isHeartbeat ? "transparent" : "var(--gr3)" }} />}
            </div>

            <div className="cd-tl-content">
              <div style={{ display: "flex", alignItems: "baseline", gap: 7, marginBottom: 3 }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 13, fontWeight: 700, color: isPattern ? "var(--pu)" : agentColor }}>
                  {event.agent_id ?? "heartbeat"}
                </span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr2)", letterSpacing: "0.06em" }}>
                  {event.event_type}{isPattern ? " ⭐" : ""}
                </span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr2)", marginLeft: "auto" }}>
                  t+{timeDiff}s
                </span>
              </div>

              {!!payload?.input_summary && (
                <div style={{ fontSize: 14, color: "var(--gr)", fontWeight: 300, lineHeight: 1.5, marginBottom: 3 }}>
                  {String(payload.input_summary)}
                </div>
              )}

              {isPattern && !!payload?.pattern_ids && (
                <div className="cd-tl-code cd-tl-code-pu" style={{ marginTop: 3 }}>
                  pattern_ids: {JSON.stringify(payload.pattern_ids)}
                </div>
              )}

              {!!payload?.output_summary && (
                <div className="cd-tl-code cd-tl-code-green" style={{ marginTop: 3 }}>
                  {String(payload.output_summary)}
                  {payload.output_id ? ` · id: "${payload.output_id}"` : ""}
                </div>
              )}

              {!!payload?.tool_name && (
                <div className="cd-tl-code" style={{ marginTop: 3 }}>
                  {String(payload.tool_name)} · {JSON.stringify(payload.args ?? {})}
                </div>
              )}

              {!!payload?.error_message && (
                <div className="cd-tl-code" style={{ marginTop: 3, color: "var(--rd)", borderColor: "rgba(229,56,59,0.3)" }}>
                  ERROR: {String(payload.error_message)}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
