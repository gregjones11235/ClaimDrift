export type AgentId =
  | "claim_extractor"
  | "drift_analyzer"
  | "citation_finder"
  | "notifier"
  | "memory_synthesizer";

export type SseEventType =
  | "heartbeat"
  | "agent.started"
  | "agent.tool_call"
  | "agent.pattern_retrieved"
  | "agent.step"
  | "agent.completed"
  | "agent.failed";

export type SeverityTier = "pending" | "central" | "comparative" | "peripheral";

export type ClaimType =
  | "qualitative"
  | "quantitative"
  | "causal"
  | "correlational"
  | "hedged";

export type DiffType =
  | "claim_disappeared"
  | "claim_added"
  | "numerical_shift"
  | "hedging_added"
  | "hedging_removed"
  | "claim_reversed";

export interface SseEvent<TPayload = Record<string, unknown>> {
  event_type: SseEventType;
  agent_id: AgentId | null;
  drift_event_id: string | null;
  timestamp: string;
  payload: TPayload;
}

export interface DriftEventSummary {
  event_id: string;
  preprint_doi: string;
  preprint_version_compared: string;
  published_doi: string;
  drift_summary: string;
  materiality_score: number;
  detected_at: string;
}

export interface AffectedCitation {
  affected_citation_id: string;
  drift_event_id: string;
  citing_paper_doi: string;
  citing_paper_title: string;
  severity_tier: SeverityTier;
  severity_reasoning: string;
}
