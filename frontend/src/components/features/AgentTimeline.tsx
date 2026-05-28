"use client";

import { SseEvent } from "@/types/claimdrift";
import { useEffect, useRef, useState } from "react";
import { Loader2, Check, X, RefreshCcw } from "lucide-react";

export function AgentTimeline({ events }: { events: SseEvent[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [startTime, setStartTime] = useState<number | null>(null);

  useEffect(() => {
    // Anchor the timeline to the *first event's own timestamp*, not the wall
    // clock when the page rendered. Replayed historical streams (e.g. tailing
    // a drift_event that finished hours ago) would otherwise show large
    // negative offsets like `t+-691s`.
    if (events.length > 0 && startTime === null) {
      const firstTs = new Date(events[0].timestamp).getTime();
      if (!Number.isNaN(firstTs)) {
        setStartTime(firstTs);
      }
    }
  }, [events, startTime]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events]);

  // Note: the page that owns this component handles the "nothing to show"
  // copy because it has the context to explain *why* (pre-adapter event, SSE
  // disconnected, etc.). We just return null and let the parent render its
  // own state.
  if (events.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col h-[500px] overflow-y-auto pr-4 scroll-smooth" ref={scrollRef}>
      {events.map((event, i) => {
        const timeDiff = startTime ? ((new Date(event.timestamp).getTime() - startTime) / 1000).toFixed(1) : "0.0";
        const isLast = i === events.length - 1;
        const payload = event.payload as any;

        if (event.event_type === "agent.pattern_retrieved") {
          return (
             <div key={i} className="flex flex-col">
              <div className="flex gap-3 p-3 rounded-none border border-[#E8590C] bg-[#FFF4E6]">
                <div className="w-2.5 h-2.5 rounded-full bg-[#E8590C] mt-1.5 flex-shrink-0" />
                <div className="flex-1">
                  <div className="text-[13px] font-sans font-medium text-[#E8590C] flex justify-between items-center">
                    <div>
                      {event.agent_id} <span className="text-[10px] font-mono font-normal">agent.pattern_retrieved</span>
                    </div>
                    <span className="text-[10px] text-[#666] font-mono">t+{timeDiff}s</span>
                  </div>
                  <div className="text-[11px] font-sans text-[#666] mt-0.5">
                    Memory loop triggered — pattern retrieved from drift_patterns index
                  </div>
                  <div className="mt-2 text-[11px] font-mono text-black bg-[#222] text-[#E8590C] p-2 rounded-none">
                    <div>pattern_ids: {JSON.stringify(payload.pattern_ids)} &middot; similarity_scores: {JSON.stringify(payload.similarity_scores)}</div>
                  </div>
                </div>
              </div>
              {!isLast && <div className="w-[1px] h-3 bg-black/20 ml-[17px] my-1" />}
            </div>
          );
        }

        if (event.event_type === "heartbeat") {
          return (
            <div key={i} className="flex flex-col mt-2 opacity-50">
              <div className="flex gap-3 p-3 rounded-none border border-dashed border-[#868E96] bg-[#F1F3F5] items-start">
                <div className="w-2.5 h-2.5 rounded-full bg-[#868E96] mt-1.5 flex-shrink-0" />
                <div className="flex-1">
                  <div className="flex justify-between items-start">
                    <div className="text-[13px] font-sans font-medium text-[#495057]">
                      heartbeat <span className="text-[10px] font-mono font-normal text-[#868E96]">agent_id: null</span>
                    </div>
                    <span className="text-[10px] font-mono text-[#868E96]">t+{timeDiff}s</span>
                  </div>
                  <div className="text-[11px] font-sans text-[#868E96] mt-0.5">
                    Pipeline complete — keepalive
                  </div>
                </div>
              </div>
            </div>
          );
        }

        let containerClass = "flex gap-3 p-3 rounded-none border ";
        let dotClass = "w-2.5 h-2.5 rounded-full mt-1.5 flex-shrink-0 ";
        let titleContainerClass = "text-[13px] font-sans font-medium flex items-center gap-1.5 ";
        let subTextClass = "text-[11px] font-sans mt-0.5 ";
        let outClass = "text-[11px] font-mono mt-2 p-1.5 rounded-none ";
        let isDashed = false;
        
        if (event.agent_id === "citation_finder") {
          // run state
          containerClass += "border-[#1971C2] bg-[#E7F5FF]";
          dotClass += "bg-[#1971C2]";
          titleContainerClass += "text-[#1971C2]";
          subTextClass += "text-[#1971C2]";
          outClass += "bg-[#1971C2]/10 text-[#1971C2]";
        } else if (event.agent_id === "notifier") {
          // wait state
          containerClass += "border-[#868E96] border-dashed bg-[#F1F3F5]";
          dotClass += "bg-[#868E96]";
          titleContainerClass += "text-[#495057]";
          subTextClass += "text-[#868E96]";
          outClass += "bg-[#E9ECEF] text-[#495057]";
          isDashed = true;
        } else {
          // done state
          containerClass += "border-[#2F9E44] bg-white";
          dotClass += "bg-[#2F9E44]";
          titleContainerClass += "text-[#212529]";
          subTextClass += "text-[#868E96]";
          outClass += "bg-[#F8F9FA] border border-[#E9ECEF] text-[#495057]";
        }

        return (
          <div key={i} className={`flex flex-col ${isDashed ? 'opacity-50' : ''}`}>
            <div className={containerClass}>
              <div className={dotClass} />
              <div className="flex-1">
                <div className="flex justify-between items-start">
                  <div className={titleContainerClass}>
                    {event.agent_id} <span className="text-[10px] font-mono text-[#868E96] font-normal ml-1">{event.event_type}</span>
                  </div>
                  <span className="text-[10px] font-mono text-[#868E96]">t+{timeDiff}s</span>
                </div>
                
                {payload?.input_summary && (
                  <div className={subTextClass}>
                    payload.input_summary: "{payload.input_summary}"
                  </div>
                )}
                
                {payload?.output_summary && (
                  <div className={outClass}>
                    output_summary: "{payload.output_summary}"
                    {payload.output_id && ` · output_id: "${payload.output_id}"`}
                  </div>
                )}
              </div>
            </div>
            {!isLast && (
              <div className={`w-[1px] h-3 ml-[17px] my-1 ${isDashed ? 'bg-transparent border-l border-dashed border-[#868E96]' : 'bg-black/20'}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}
