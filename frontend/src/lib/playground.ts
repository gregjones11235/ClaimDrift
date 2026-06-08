// Client for the A/B-test Playground backend (apps/playground/server.py).
//
// This is a SEPARATE service from the BFF (apps/bff): the BFF tails persisted
// agent_events for the production dashboard, whereas the Playground backend
// runs the drift_analyzer A/B three-state experiment live and streams its own
// progress events. Hence its own base URL — do NOT route it through the BFF.
export const PLAYGROUND_URL =
  process.env.NEXT_PUBLIC_PLAYGROUND_URL ?? "http://127.0.0.1:8799";

// One state's result, mirrors apps/playground/server.py `state.done` payload.
export interface StateReading {
  calibrated_materiality: number | null;
  baseline_materiality_without_memory?: number | null;
  materiality_score?: number | null;
  retrieved_patterns_used: string[];
  memory_pattern_ids: string[];
  rationale?: string | null;
  parse_error?: boolean;
  // Set when drift_analyzer (gemini-2.5-pro) returned empty after retries due to
  // a Vertex Dynamic-Shared-Quota 429. "quota_confirmed" = seen in engine logs;
  // "quota_suspected" = empty stream but logs unavailable. quota_detail is the
  // human-readable explanation shown on the card. See server._run_drift_analyzer.
  quota_error?: "quota_confirmed" | "quota_suspected";
  quota_detail?: string;
}

export type StatePhase =
  | "idle"
  | "injected"
  | "analyzing"
  | "done";

export interface StateView extends Partial<StateReading> {
  key: string;
  label: string;
  support: number | null;
  phase: StatePhase;
  injectDetail?: string;
  events?: number;
}

export interface RunMeta {
  case?: { registry_id: string; title: string; published_doi: string };
  states: { key: string; label: string; support: number | null }[];
}

// Shared manual SSE reader. We use fetch + a manual reader (not EventSource)
// because EventSource cannot stream a long single GET cleanly across all the
// named event types we emit, and we want explicit abort control. `onEvent`
// fires for every parsed frame; returns an abort function.
function _streamSSE(
  url: string,
  onEvent: (type: string, data: Record<string, unknown>) => void,
  onError: (msg: string) => void,
  onDone: () => void,
): () => void {
  const ctrl = new AbortController();

  (async () => {
    try {
      const res = await fetch(url, {
        signal: ctrl.signal,
        headers: { Accept: "text/event-stream" },
      });
      if (!res.ok || !res.body) {
        onError(`Playground backend returned ${res.status}`);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line.
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);

          let evType = "message";
          const dataLines: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) evType = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (dataLines.length) {
            try {
              onEvent(evType, JSON.parse(dataLines.join("\n")));
            } catch {
              /* ignore unparseable frame */
            }
          }
        }
      }
      onDone();
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        onError((e as Error).message || "stream failed");
      }
    }
  })();

  return () => ctrl.abort();
}

// Run the three-state A/B (experiment 1) via the Playground SSE endpoint.
export function runPlayground(
  onEvent: (type: string, data: Record<string, unknown>) => void,
  onError: (msg: string) => void,
  onDone: () => void,
): () => void {
  return _streamSSE(`${PLAYGROUND_URL}/api/playground/run`, onEvent, onError, onDone);
}

// --- Experiment 2: 5-agent orchestration ------------------------------------

export type NodeId =
  | "claim_extractor"
  | "drift_analyzer"
  | "citation_finder"
  | "notifier"
  | "memory_synthesizer";

export type NodePhase = "idle" | "active" | "done" | "error";

// One lane of a node (fan-out nodes — claim_extractor ×2, notifier ×N — have
// multiple lanes; single nodes have exactly one lane index 0).
export interface NodeLane {
  phase: NodePhase;
  action?: string; // current tool, e.g. "search_drift_patterns"
  summary?: string; // last output line
  error?: string;
}

export interface NodeView {
  id: NodeId;
  label: string;
  fanout: boolean;
  lanes: Record<number, NodeLane>;
}

export interface EmailEvent {
  to: string;
  subject: string;
  status: "sending" | "sent" | "failed";
  message_id?: string;
  error?: string;
}

export interface OrchestrationMeta {
  case?: { preprint_doi: string; published_doi: string; title: string };
  pipeline: { id: NodeId; label: string; fanout: boolean }[];
  judge_email?: string;
}

// Run the full 5-agent supervisor pipeline live. `email` is the judge's address
// for the drift-alert mail. Same manual-SSE machinery as runPlayground.
export function runOrchestration(
  email: string,
  onEvent: (type: string, data: Record<string, unknown>) => void,
  onError: (msg: string) => void,
  onDone: () => void,
): () => void {
  const url = `${PLAYGROUND_URL}/api/playground/orchestrate?email=${encodeURIComponent(email)}`;
  return _streamSSE(url, onEvent, onError, onDone);
}
