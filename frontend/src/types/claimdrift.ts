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

export type SeverityTier = "central" | "comparative" | "peripheral";

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
  // The BFF's /api/drift-events returns the full ES _source, so the list view
  // also receives claim_diffs (the home page reads the first diff's type).
  // Optional because it is not part of the minimal summary contract.
  claim_diffs?: ClaimDiff[];
}

export interface AffectedCitation {
  affected_citation_id: string;
  drift_event_id: string;
  citing_paper_doi: string;
  citing_paper_title: string;
  citing_paper_authors: { name: string; orcid: string | null; email: string | null }[];
  scored_at: string;
  severity_tier: SeverityTier;
  severity_reasoning: string;
}

export interface NumericalDelta {
  metric: string;
  preprint_value: number;
  published_value: number;
  absolute_delta: number;
  relative_delta: number;
}

export interface ClaimDiff {
  diff_type: DiffType;
  preprint_claim_id: string;
  published_claim_id: string;
  preprint_text: string;
  published_text: string;
  change_description: string;
  numerical_delta?: NumericalDelta;
}

export interface RetrievedPattern {
  pattern_id: string;
  pattern_description: string;
  pattern_type: string;
  domain_tags: string[];
  support_count: number;
  similarity_score: number;
}

export interface DriftEvent extends DriftEventSummary {
  claim_diffs: ClaimDiff[];
  retrieved_patterns: RetrievedPattern[];
}

// Status enum matches notification_log mapping (contracts.md §2.2.6) plus
// "skipped" which the dispatcher writes when no recipient email is available.
export type NotificationStatus =
  | "drafted"
  | "sent"
  | "bounced"
  | "failed"
  | "skipped";

export interface NotificationLog {
  affected_citation_id: string;
  drift_event_id: string;
  recipient_email: string;
  subject: string;
  body: string;
  status: NotificationStatus;
  drafted_at?: string | null;
  sent_at: string | null;
  error_message: string | null;
}

// Whole-index rollups served by the BFF's /api/stats (ES aggregations).
// These reflect the full population, unlike the most-recent-100 /api/drift-events page.
export interface DashboardStats {
  drift_events_total: number;
  high_severity_count: number;
  avg_materiality_score: number;
  affected_citations_total: number;
  notifications_total: number;
  notifications_sent: number;
  patterns_total: number;
  top_pattern_type: string | null;
}

export interface DriftPattern {
  pattern_id: string;
  pattern_description: string;
  pattern_type: string;
  domain_tags: string[];
  source_event_ids: string[];
  support_count: number;
  created_at: string;
  last_updated_at: string;
}

export interface Claim {
  claim_id: string;
  parent_doi: string;
  parent_version: string;
  section: string;
  claim_idx: number;
  text: string;
  claim_type: ClaimType;
  numerical_values?: {
    metric: string;
    value: number;
    unit: string;
    comparison: string;
  }[];
  hedging_level: "none" | "weak" | "strong";
  extracted_at: string;
}
