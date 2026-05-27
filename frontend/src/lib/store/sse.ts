import { create } from "zustand";
import { SseEvent } from "@/types/claimdrift";

interface SseState {
  events: SseEvent[];
  isListening: boolean;
  startListening: (driftEventId: string) => void;
  stopListening: () => void;
  clearEvents: () => void;
}

let eventSource: EventSource | null = null;
const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL ?? "http://127.0.0.1:8787";

export const useSseStore = create<SseState>((set) => ({
  events: [],
  isListening: false,
  
  startListening: (driftEventId: string) => {
    if (eventSource) {
      eventSource.close();
    }
    
    set({ isListening: true, events: [] });
    
    eventSource = new EventSource(`${BFF_URL}/api/events/stream?drift_event_id=${driftEventId}`);
    
    const handler = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as SseEvent;
        set((state) => ({ events: [...state.events, data] }));
      } catch (err) {
        console.error("Failed to parse SSE", err);
      }
    };

    const namedTypes = [
      "agent.started",
      "agent.completed",
      "agent.pattern_retrieved",
      "agent.tool_call",
      "agent.step",
      "agent.failed",
      "heartbeat",
    ];

    namedTypes.forEach((type) => {
      eventSource!.addEventListener(type, handler as EventListener);
    });
    
    eventSource.onerror = () => {
      // Handle reconnect or error
      if (eventSource?.readyState === EventSource.CLOSED) {
        set({ isListening: false });
      }
    };
  },
  
  stopListening: () => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    set({ isListening: false });
  },
  
  clearEvents: () => set({ events: [] })
}));
