"use client";

import { useSseStore } from "@/lib/store/sse";
import { AgentTimeline } from "@/components/features/AgentTimeline";
import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";

function LiveStreamContent() {
  const { events, startListening, stopListening } = useSseStore();
  const searchParams = useSearchParams();
  const driftEventId = searchParams?.get("drift_event_id") || "demo-drift-001";

  useEffect(() => {
    startListening(driftEventId);
    return () => {
      stopListening();
    };
  }, [driftEventId, startListening, stopListening]);

  return (
    <>
      <div className="mb-4">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-[#1971C2] text-[11px] font-medium text-[#1971C2]">
          <div className="w-2 h-2 rounded-full bg-[#1971C2] animate-pulse" />
          LIVE — real SSE events
        </div>
      </div>

      <AgentTimeline events={events} />
    </>
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
          Real-time observability into multi-agent drift analysis.
        </div>
      </div>
      
      <Suspense fallback={<div className="text-[13px] text-[#666] italic">Loading stream...</div>}>
        <LiveStreamContent />
      </Suspense>
    </div>
  );
}
