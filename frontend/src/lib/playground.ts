// Client for the Playground backend (apps/playground/server.py).
//
// This is a SEPARATE service from the BFF (apps/bff): the BFF tails persisted
// agent_events for the production dashboard, whereas the Playground backend
// runs the supervisor orchestration live and streams its own progress events.
// Hence its own base URL — do NOT route it through the BFF. (The memory-loop
// A/B endpoint /api/playground/run was removed with the pattern library, P1.9.)
// The playground backend is reached through the frontend's /api/playground/* rewrite (next.config.ts), so the session
// cookie goes along; the backend refuses a run without a logged-in session.

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

// --- 5-agent orchestration ---------------------------------------------------

export type NodeId =
  | "claim_extractor"
  | "drift_analyzer"
  | "citation_finder"
  | "notifier";

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
// for the drift-alert mail.
export function runOrchestration(
  email: string,
  onEvent: (type: string, data: Record<string, unknown>) => void,
  onError: (msg: string) => void,
  onDone: () => void,
): () => void {
  const url = `/api/playground/orchestrate?email=${encodeURIComponent(email)}`;
  return _streamSSE(url, onEvent, onError, onDone);
}
