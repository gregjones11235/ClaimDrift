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

// Map materiality_score (0–1) to a severity tier + colour, matching the
// dashboard's thresholds: ≥0.7 high (red), ≥0.4 medium (orange), else low.
function severityOf(score: number): { tier: string; color: string; dot: string } {
  if (score >= 0.7) return { tier: "HIGH",   color: "var(--rd)",  dot: "🔴" };
  if (score >= 0.4) return { tier: "MEDIUM", color: "var(--or)",  dot: "🟠" };
  return { tier: "LOW", color: "var(--grn)", dot: "🟢" };
}

// The first claim diff's type is the most representative single label for the
// event (the dashboard's DIFF_TYPE column uses the same convention).
function primaryDiffType(ev: DriftEventSummary): string {
  return ev.claim_diffs?.[0]?.diff_type ?? "—";
}

function shortDate(iso: string): string {
  // detected_at is an ISO string; show YYYY-MM-DD without pulling in a date lib.
  return (iso || "").slice(0, 10);
}

// Plain-text label for a native <option>: severity dot + materiality + diff
// type + full preprint DOI + date. Options cannot wrap or be styled, but the
// front-loaded fields (severity, score, diff type) survive truncation on narrow
// widths. The DOI is shown in full and prefixed with "doi:" so it isn't
// mistaken for the trailing date. Full detail is in <SelectedEventCard>.
function driftEventOptionLabel(ev: DriftEventSummary): string {
  const { dot } = severityOf(ev.materiality_score);
  return `${dot} ${ev.materiality_score.toFixed(2)}  ${primaryDiffType(ev)}  doi:${ev.preprint_doi}  ${shortDate(ev.detected_at)}`;
}

// Rich, human-readable card describing the currently selected event. This is
// where the full drift_summary, severity bar, DOIs and date are shown — the
// detail a native <option> cannot render.
function SelectedEventCard({ ev }: { ev: DriftEventSummary }) {
  const sev = severityOf(ev.materiality_score);
  const pct = Math.round(ev.materiality_score * 100);
  return (
    <div style={{ marginBottom: 16, padding: "12px 14px", border: "1px solid var(--gr3)", borderLeft: `2px solid ${sev.color}`, background: "var(--bk2)" }}>
      {/* Header row: severity tier + materiality bar + diff type + date */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", color: sev.color, border: `1px solid ${sev.color}`, padding: "2px 7px" }}>
          {sev.tier}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span className="specimen" style={{ color: "var(--gr2)" }}>materiality</span>
          <div style={{ width: 90, height: 4, background: "var(--gr3)", position: "relative" }}>
            <div style={{ position: "absolute", left: 0, top: 0, height: "100%", width: `${pct}%`, background: sev.color }} />
          </div>
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, fontWeight: 700, color: sev.color }}>{ev.materiality_score.toFixed(2)}</span>
        </div>
        <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--wh2)", border: "1px solid var(--gr3)", padding: "2px 7px" }}>
          {primaryDiffType(ev)}
        </span>
        <span className="specimen" style={{ color: "var(--gr2)", marginLeft: "auto" }}>{shortDate(ev.detected_at)}</span>
      </div>

      {/* Full drift summary */}
      <p style={{ fontSize: 12, lineHeight: 1.6, color: "var(--wh2)", margin: "0 0 8px" }}>
        {ev.drift_summary || "No drift summary available for this event."}
      </p>

      {/* DOIs (preprint → published), labelled so they read as DOIs not dates */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", fontFamily: "var(--mono)", fontSize: 9, color: "var(--gr)" }}>
        <span className="specimen" style={{ color: "var(--gr3)" }}>doi</span>
        <span style={{ color: "var(--gr2)" }}>{ev.preprint_doi}</span>
        <span style={{ color: "var(--gr3)" }}>→</span>
        <span style={{ color: "var(--gr2)" }}>{ev.published_doi}</span>
      </div>
    </div>
  );
}

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

  const selectedEvent = allEvents.find((e) => e.event_id === driftEventId) ?? null;

  return (
    <>
      {/* Picker row */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, padding: "10px 14px", border: "1px solid var(--gr3)", background: "var(--bk2)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 10px", border: `1px solid ${driftEventId ? "var(--grn)" : "var(--gr3)"}` }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: driftEventId ? "var(--grn)" : "var(--gr2)", animation: driftEventId ? "pulse-dot 1.5s ease-out infinite" : "none" }} />
          <span className="specimen" style={{ color: driftEventId ? "var(--grn)" : "var(--gr2)" }}>
            {driftEventId ? "Streaming agent_events" : "Select a drift event"}
          </span>
        </div>

        <span className="specimen" style={{ color: "var(--gr2)", whiteSpace: "nowrap" }}>drift event:</span>
        {/* Native <select> kept for keyboard/a11y/mobile reliability. Each
            <option> is plain single-line text (no rich markup possible), so it
            only carries scannable identifiers: severity dot + materiality +
            diff type + short DOI + date. The full summary lives in the rich
            card below. */}
        <select value={driftEventId} onChange={onSelect} disabled={loadingList || !!listError}
          style={{ flex: 1, background: "var(--bk3)", border: "1px solid var(--gr3)", color: "var(--wh2)", fontFamily: "var(--mono)", fontSize: 11, padding: "6px 8px", outline: "none", cursor: "pointer" }}>
          <option value="" disabled>
            {loadingList ? "loading…" : listError ? listError : "— pick one —"}
          </option>
          {allEvents.map((e) => (
            <option key={e.event_id} value={e.event_id}>
              {driftEventOptionLabel(e)}
            </option>
          ))}
        </select>
      </div>

      {/* Rich card for the currently selected event. */}
      {selectedEvent && <SelectedEventCard ev={selectedEvent} />}

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
