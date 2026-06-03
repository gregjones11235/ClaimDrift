"use client";

import { useSseStore } from "@/lib/store/sse";
import { AgentTimeline } from "@/components/features/AgentTimeline";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getDriftEvents } from "@/lib/api/client";
import { DriftEventSummary } from "@/types/claimdrift";

function LiveStreamContent() {
  const { events, isListening, startListening, stopListening } = useSseStore();
  const searchParams = useSearchParams();
  const router = useRouter();
  const driftEventId = searchParams?.get("drift_event_id") ?? "";

  // The SSE stream returns two kinds of frames: the §6.1 envelopes the
  // dispatcher writes to agent_events (the actual pipeline events we want to
  // show) and 15s heartbeats to keep the connection alive. If we only ever see
  // heartbeats, the SSE channel is healthy but agent_events has no rows for
  // this drift_event_id — which happens when the drift event was produced
  // before the SSE adapter shipped (2026-05-28; see contracts.md §6.3).
  const dataEvents = events.filter((e) => e.event_type !== "heartbeat");
  const heartbeatCount = events.length - dataEvents.length;

  // Drift events list is fetched client-side here so the picker stays in sync
  // when this page is the user's entry point. We pick the most recent event
  // as the default once the list lands, so the page is immediately useful
  // without a manual selection.
  const [allEvents, setAllEvents] = useState<DriftEventSummary[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDriftEvents()
      .then(({ items }) => {
        if (cancelled) return;
        // Sort newest-first by detected_at.
        const sorted = [...items].sort(
          (a, b) => new Date(b.detected_at).getTime() - new Date(a.detected_at).getTime()
        );
        setAllEvents(sorted);
        setLoadingList(false);
        // Auto-select the latest if URL didn't pin one.
        if (!driftEventId && sorted.length > 0) {
          const params = new URLSearchParams(searchParams?.toString() ?? "");
          params.set("drift_event_id", sorted[0].event_id);
          router.replace(`/live?${params.toString()}`);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setListError(err?.message ?? "failed to load drift events");
        setLoadingList(false);
      });
    return () => {
      cancelled = true;
    };
  }, [driftEventId, router, searchParams]);

  useEffect(() => {
    if (!driftEventId) return;
    startListening(driftEventId);
    return () => {
      stopListening();
    };
  }, [driftEventId, startListening, stopListening]);

  const onSelect = (e: { target: { value: string } }) => {
    const id = e.target.value;
    if (!id) return;
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    params.set("drift_event_id", id);
    router.replace(`/live?${params.toString()}`);
  };

  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-4 flex-wrap">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-[#1971C2] text-[11px] font-medium text-[#1971C2]">
          <div className="w-2 h-2 rounded-full bg-[#1971C2] animate-pulse" />
          {driftEventId ? "LIVE — tailing agent_events" : "Select a drift event to tail"}
        </div>
        <div className="flex items-center gap-2 text-[12px] font-mono">
          <label htmlFor="drift-picker" className="text-[#666]">
            drift_event_id:
          </label>
          <select
            id="drift-picker"
            value={driftEventId}
            onChange={onSelect}
            disabled={loadingList || !!listError}
            className="border border-black bg-white px-2 py-1 text-[12px] font-mono max-w-[480px] truncate"
          >
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
      </div>

      {!driftEventId ? (
        <div className="flex items-center justify-center h-48 border border-black bg-white text-[13px] text-[#666] italic font-sans">
          {loadingList
            ? "Loading drift events…"
            : listError
            ? `Could not load drift events: ${listError}`
            : allEvents.length === 0
            ? "No drift events yet. Run the pipeline to produce one."
            : "Pick a drift_event_id above to start tailing."}
        </div>
      ) : dataEvents.length > 0 ? (
        <AgentTimeline events={events} />
      ) : (
        <LiveStreamWaitingPanel
          driftEventId={driftEventId}
          isListening={isListening}
          heartbeatCount={heartbeatCount}
        />
      )}
    </>
  );
}

function LiveStreamWaitingPanel({
  driftEventId,
  isListening,
  heartbeatCount,
}: {
  driftEventId: string;
  isListening: boolean;
  heartbeatCount: number;
}) {
  // Three meaningful states, all distinguishable from what we've observed so far:
  //   1. SSE not yet connected (isListening=false, 0 heartbeats) — usually a
  //      transient connection-setup state, but if it persists it means the
  //      BFF or network is unreachable.
  //   2. SSE connected, no frames yet (isListening=true, 0 heartbeats) —
  //      brief window before the first poll cycle (~1s).
  //   3. SSE connected, only heartbeats (isListening=true, heartbeats > 0) —
  //      the stream is healthy but agent_events has no rows for this drift.
  //      Almost always because the drift was produced before the SSE adapter
  //      shipped on 2026-05-28 (contracts.md §6.3 changelog).
  const connecting = !isListening && heartbeatCount === 0;
  const justOpened = isListening && heartbeatCount === 0;
  const stale = heartbeatCount > 0;

  return (
    <div className="border border-black bg-white p-6 space-y-4">
      <div className="flex items-center gap-2">
        <div
          className={`w-2 h-2 rounded-full ${
            connecting
              ? "bg-[#666]"
              : stale
              ? "bg-[#E8590C]"
              : "bg-[#1971C2] animate-pulse"
          }`}
        />
        <div className="text-[13px] font-medium font-sans text-black">
          {connecting && "Connecting to BFF…"}
          {justOpened && "Stream open — waiting for first frame."}
          {stale && "Stream healthy, but this drift event has no recorded agent activity."}
        </div>
      </div>

      {stale && (
        <>
          <div className="text-[12px] font-sans text-[#666] leading-[1.6]">
            The Live view tails the{" "}
            <code className="font-mono px-1 bg-[#F5F5F5] border border-black">agent_events</code>{" "}
            index, which is written by the Cloud Run dispatcher as it streams
            events from the supervisor on Vertex AI Agent Engine. That index
            was introduced when the SSE adapter shipped on 2026-05-28; drift
            events processed before that date have no rows in it, which is
            what we&rsquo;re seeing here for
            <code className="font-mono mx-1 px-1 bg-[#F5F5F5] border border-black">
              {driftEventId.slice(0, 8)}…
            </code>
            ({heartbeatCount} heartbeat{heartbeatCount === 1 ? "" : "s"} received,
            zero data frames).
          </div>
          <div className="text-[12px] font-sans text-[#666] leading-[1.6]">
            To see real agent activity, you have two paths:
          </div>
          <ul className="text-[12px] font-sans text-[#666] leading-[1.7] list-disc ml-5 space-y-1">
            <li>
              Trigger a new dispatch:{" "}
              <code className="font-mono px-1 bg-[#F5F5F5] border border-black">
                POST /dispatch
              </code>{" "}
              on the dispatcher with a fresh{" "}
              <code className="font-mono px-1 bg-[#F5F5F5] border border-black">
                (preprint_doi, published_doi)
              </code>{" "}
              pair. The pipeline takes ~200 s and writes envelopes to{" "}
              <code className="font-mono px-1 bg-[#F5F5F5] border border-black">
                agent_events
              </code>{" "}
              in real time as they arrive.
            </li>
            <li>
              Run the BFF in replay mode:{" "}
              <code className="font-mono px-1 bg-[#F5F5F5] border border-black">
                SSE_REPLAY_GOLDEN=1
              </code>{" "}
              replays the checked-in T1 stream{" "}
              <code className="font-mono px-1 bg-[#F5F5F5] border border-black">
                apps/dispatcher/tests/golden/stream_amblyopia_v2.jsonl
              </code>{" "}
              through the production translator. No GCP credentials required —
              this is the evaluator-friendly path.
            </li>
          </ul>
        </>
      )}

      {justOpened && (
        <div className="text-[12px] font-sans text-[#666] italic">
          The first heartbeat arrives within ~15 s. If none does, the BFF is not
          reachable from this browser — check{" "}
          <code className="font-mono px-1 bg-[#F5F5F5] border border-black">
            NEXT_PUBLIC_BFF_URL
          </code>
          .
        </div>
      )}

      {connecting && (
        <div className="text-[12px] font-sans text-[#666] italic">
          Opening the EventSource to{" "}
          <code className="font-mono px-1 bg-[#F5F5F5] border border-black">
            /api/events/stream
          </code>
          …
        </div>
      )}
    </div>
  );
}

export default function LiveStreamPage() {
  return (
    <div className="max-w-3xl">
      <div className="mb-6">
        <h1 className="text-[22px] font-medium font-sans text-black mb-2">
          Pipeline Execution Stream
        </h1>
        <div className="text-[13px] text-[#666] font-sans">
          Real-time observability into multi-agent drift analysis. Tails the
          <code className="font-mono mx-1 px-1 bg-[#F5F5F5] border border-black">agent_events</code>
          index via SSE; see contracts.md §6.3.
        </div>
      </div>

      <Suspense fallback={<div className="text-[13px] text-[#666] italic">Loading stream...</div>}>
        <LiveStreamContent />
      </Suspense>
    </div>
  );
}
