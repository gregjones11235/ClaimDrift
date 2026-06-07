"use client";

import { useSseStore } from "@/lib/store/sse";
import { AgentTimeline } from "@/components/features/AgentTimeline";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getDriftEvents } from "@/lib/api/client";
import { DriftEventSummary } from "@/types/claimdrift";
import Oscilloscope from "@/components/landing/Oscilloscope";

const AGENT_LEGEND = [
  { label: "claim_extractor",    color: "var(--y)"  },
  { label: "drift_analyzer",     color: "var(--rd)" },
  { label: "citation_finder",    color: "var(--bl)" },
  { label: "notifier",           color: "var(--grn)"},
  { label: "memory_synthesizer", color: "var(--pu)" },
  { label: "pattern_retrieved ⭐", color: "var(--pu)", glow: true },
];

function LiveStreamContent() {
  const { events, isListening, startListening, stopListening } = useSseStore();
  const searchParams = useSearchParams();
  const router = useRouter();
  const driftEventId = searchParams?.get("drift_event_id") ?? "";

  const [allEvents, setAllEvents] = useState<DriftEventSummary[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const dataEvents = events.filter((e) => e.event_type !== "heartbeat");
  const heartbeatCount = events.length - dataEvents.length;

  useEffect(() => {
    let cancelled = false;
    getDriftEvents()
      .then(({ items }) => {
        if (cancelled) return;
        const sorted = [...items].sort((a, b) => new Date(b.detected_at).getTime() - new Date(a.detected_at).getTime());
        setAllEvents(sorted);
        setLoadingList(false);
        if (!driftEventId && sorted.length > 0) {
          const params = new URLSearchParams(searchParams?.toString() ?? "");
          params.set("drift_event_id", sorted[0].event_id);
          router.replace(`/live?${params.toString()}`);
        }
      })
      .catch((err) => { if (!cancelled) { setListError(err?.message ?? "failed"); setLoadingList(false); } });
    return () => { cancelled = true; };
  }, [driftEventId, router, searchParams]);

  useEffect(() => {
    if (!driftEventId) return;
    startListening(driftEventId);
    return () => stopListening();
  }, [driftEventId, startListening, stopListening]);

  const onSelect = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const id = e.target.value;
    if (!id) return;
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    params.set("drift_event_id", id);
    router.replace(`/live?${params.toString()}`);
  };

  return (
    <>
      {/* Picker row */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16, padding: "10px 14px", border: "1px solid var(--gr3)", background: "var(--bk2)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 10px", border: `1px solid ${driftEventId ? "var(--grn)" : "var(--gr3)"}` }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: driftEventId ? "var(--grn)" : "var(--gr2)", animation: driftEventId ? "pulse-dot 1.5s ease-out infinite" : "none" }} />
          <span className="specimen" style={{ color: driftEventId ? "var(--grn)" : "var(--gr2)" }}>
            {driftEventId ? "LIVE — tailing agent_events" : "Select a drift event"}
          </span>
        </div>

        <span className="specimen" style={{ color: "var(--gr2)", whiteSpace: "nowrap" }}>drift_event_id:</span>
        <select value={driftEventId} onChange={onSelect} disabled={loadingList || !!listError}
          style={{ flex: 1, background: "var(--bk3)", border: "1px solid var(--gr3)", color: "var(--wh2)", fontFamily: "var(--mono)", fontSize: 9, padding: "5px 8px", outline: "none", cursor: "pointer" }}>
          <option value="" disabled>
            {loadingList ? "loading…" : listError ? listError : "— pick one —"}
          </option>
          {allEvents.map((e) => (
            <option key={e.event_id} value={e.event_id}>
              {e.event_id.slice(0, 8)}… · {e.preprint_doi}
            </option>
          ))}
        </select>
      </div>

      {/* Timeline panel */}
      <div className="cd-panel">
        <div className="cd-panel-header">
          <span className="cd-panel-label">agent_events stream · SSE</span>
          <span className="specimen">contracts.md §6.3 · sse_adapter.py</span>
        </div>

        {/* Agent legend */}
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", padding: "9px 14px", borderBottom: "1px solid var(--gr3)", background: "var(--bk3)" }}>
          {AGENT_LEGEND.map(({ label, color, glow }) => (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: 5, fontFamily: "var(--mono)", fontSize: 8, color: "var(--gr)" }}>
              <div style={{ width: 7, height: 7, borderRadius: "50%", background: color, boxShadow: glow ? `0 0 6px ${color}` : "none" }} />
              {label}
            </div>
          ))}
        </div>

        {/* Timeline or waiting state */}
        {!driftEventId ? (
          <div style={{ padding: 24, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr)", fontStyle: "italic" }}>
            Pick a drift_event_id above to start tailing.
          </div>
        ) : dataEvents.length > 0 ? (
          <AgentTimeline events={events} />
        ) : (
          <div style={{ padding: "16px 16px", border: "1px solid var(--gr3)", margin: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <div style={{ width: 7, height: 7, borderRadius: "50%", background: isListening ? "var(--bl)" : "var(--gr2)", animation: isListening ? "pulse-dot 1.5s ease-out infinite" : "none" }} />
              <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--wh2)" }}>
                {!isListening ? "Connecting to BFF…" : heartbeatCount === 0 ? "Stream open — waiting for first frame." : "Stream healthy, but no agent_events for this drift_event_id."}
              </span>
            </div>
            {heartbeatCount > 0 && (
              <div style={{ fontSize: 11, color: "var(--gr)", lineHeight: 1.7 }}>
                {heartbeatCount} heartbeat{heartbeatCount > 1 ? "s" : ""} received, zero data frames.
                This event was processed before the SSE adapter shipped (2026-05-28).
                Run <code style={{ fontFamily: "var(--mono)", background: "var(--bk3)", padding: "1px 5px", fontSize: 9 }}>SSE_REPLAY_GOLDEN=1</code> on the BFF to replay the T1 golden stream.
              </div>
            )}
          </div>
        )}

        {/* Osc footer */}
        <div style={{ borderTop: "1px solid var(--gr3)", marginTop: 24, paddingTop: 16, paddingBottom: 16 }}>
          <Oscilloscope color="rgba(62,207,142,.3)" height={32} speed={5} />
        </div>
      </div>
    </>
  );
}

export default function LiveStreamPage() {
  return (
    <div>
      <Suspense fallback={<div style={{ fontFamily: "var(--mono)", fontSize: 14, color: "var(--gr)", fontStyle: "italic" }}>Loading stream…</div>}>
        <LiveStreamContent />
      </Suspense>
    </div>
  );
}
