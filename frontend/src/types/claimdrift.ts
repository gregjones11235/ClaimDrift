// claim_extractor only runs on the long-document branch now, but historic
// agent_events (and the orchestration playground) still carry it.
export type AgentId =
  | "claim_extractor"
  | "drift_analyzer"
  | "citation_finder"
  | "notifier";

// `agent.pattern_retrieved` only appears in historic agent_events written
// before the pattern library was removed (P1.9); it is rendered generically.
export type SseEventType =
  | "heartbeat"
  | "agent.started"
  | "agent.tool_call"
  | "agent.pattern_retrieved"
  | "agent.step"
  | "agent.completed"
  | "agent.failed";

// Legacy (pre-redesign) affected_citations severity. New rows use `cites`.
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
  | "claim_reversed"
  | "outcome_switch";

// ── Severity: two separate dimensions, never merged into one number ──────────
// Abstract level: Brierley et al. 2022 three-class scale.
export type AbstractSeverityClass = "no_change" | "minor" | "major";
// Full text: four tiers from the drift_analyzer materiality score.
export type FulltextTier = "minor" | "medium" | "significant" | "major";

export type RootCause =
  | "definition_change"
  | "analysis_removed"
  | "reporting_choice"
  | "model_respecified"
  | "outcome_redefinition"
  | "method_change"
  | "generality_reversed"
  | "data_revision"
  | "wording_only";

export type ReviewStatus = "not_required" | "pending" | "approved" | "rejected";

// Event reasons + citation reasons (claimdrift/review.py). Unknown strings are
// still rendered, so this stays open-ended.
export type ReviewReason =
  | "quote_unverified"
  | "severity_mismatch"
  | "high_severity_notify"
  | "withdrawn_version"
  | "long_document_branch"
  | "reanalyzed_after_review"
  | "unclear"
  | "outgoing_notification";

export type CitesClass =
  | "superseded"
  | "current"
  | "flagged_as_previous"
  | "indirect"
  | "not_relying"
  | "unclear";

export type CitationRole = "model_input" | "reported_as_fact" | "background" | "unknown";
export type FoundVia = "prefetch" | "search_more" | "chain" | "semantic";
export type CitationFlag = "F1" | "F2" | "F3";
export type NotifyPriority = "high" | "normal" | "low";

export interface SseEvent<TPayload = Record<string, unknown>> {
  event_type: SseEventType;
  agent_id: AgentId | null;
  drift_event_id: string | null;
  timestamp: string;
  payload: TPayload;
}

export interface AnalysisRoute {
  route: "stuffed" | "claims";
  est_tokens?: number;
  limit?: number;
  forced?: boolean;
  prompt_version?: string;
}

export interface AbstractSeverity {
  class: AbstractSeverityClass | null;
  scale?: string; // "brierley_2022"
  exemplar_set?: string | null;
  model?: string | null;
  changes?: { section?: string; what?: string; degree?: string }[];
}

export interface FulltextSeverity {
  tier: FulltextTier | null;
  materiality_score?: number | null;
  prompt_version?: string | null;
  model?: string | null;
}

export interface ProvenanceRow {
  claim_diff_idx: number;
  doc_id: string;
  doi?: string | null;
  version?: string | null;
  section?: string | null;
  section_claimed?: string | null;
  quote: string;
  offset?: number | null;
  tool?: string | null;
  text_source?: string | null;
  verified: boolean;
  verified_at?: string | null;
}

export interface EventVerification {
  quotes_total: number;
  quotes_verified: number;
  status: "verified" | "partial" | "unsupported";
  method?: string | null;
  verified_at?: string | null;
}

export type CitationAnalysisStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "no_target"
  | "not_queued";

export interface CitationAnalysis {
  status: CitationAnalysisStatus;
  n_targets?: number;
  run_ids?: string[];
  checked_until?: string | null;
  n_superseded?: number;
  coverage_complete?: boolean;
}

// Fields added by the redesign. All optional: old demo events lack them.
export interface DriftEventNewFields {
  paper_id?: string | null;
  preprint_title?: string | null;
  first_author?: string | null;
  text_source?: string | null;
  analysis_route?: AnalysisRoute | null;
  abstract_severity?: AbstractSeverity | null;
  fulltext_severity?: FulltextSeverity | null;
  provenance?: ProvenanceRow[];
  verification?: EventVerification | null;
  preprint_versions?: string[] | null;
  preprint_withdrawn_versions?: string[] | null;
  review_status?: ReviewStatus | null;
  review_reasons?: string[];
  reviewer?: string | null;
  reviewed_at?: string | null;
  review_note?: string | null;
  citation_analysis?: CitationAnalysis | null;
}

export interface DriftEventSummary extends DriftEventNewFields {
  event_id: string;
  preprint_doi: string;
  preprint_version_compared: string;
  published_doi: string;
  drift_summary: string | null;
  // Full-text materiality (drift_analyzer). Can be null on new events when the
  // model returned no score.
  materiality_score: number | null;
  detected_at: string;
  // The BFF's /api/drift-events returns the full ES _source, so the list view
  // also receives claim_diffs (the home page reads the first diff's type).
  // Optional because it is not part of the minimal summary contract.
  claim_diffs?: ClaimDiff[];
}

export interface CitationVerification {
  verdict: string | null;
  condition_1?: boolean | null;
  condition_2?: boolean | null;
  condition_3?: boolean | null;
  reason?: string | null;
}

export interface CitingAuthor {
  name: string;
  orcid?: string | null;
  email?: string | null;
}

export interface AffectedCitation {
  affected_citation_id: string;
  drift_event_id: string;
  // legacy fields (old demo rows); may also be present on new rows
  citing_paper_doi?: string | null;
  citing_paper_title?: string | null;
  citing_paper_authors?: CitingAuthor[] | null;
  citation_context?: string | null;
  scored_at?: string | null;
  severity_tier?: SeverityTier | null;
  severity_reasoning?: string | null;
  // new citation analysis fields
  work_id?: string | null; // PMC id
  claim_diff_idx?: number | null;
  cites?: CitesClass | null;
  role?: CitationRole | null;
  sentence?: string | null;
  sentence_verified?: boolean | null;
  found_via?: FoundVia | string | null;
  relayed_by?: string | null;
  flags?: string[];
  unclear_rule?: string | null;
  reason?: string | null;
  judged_by?: "worker" | "batch_overflow" | null;
  needs_notification?: boolean | null;
  notify_priority?: NotifyPriority | null;
  verification?: CitationVerification | null;
  review_status?: ReviewStatus | null;
  review_reasons?: string[];
  reviewer?: string | null;
  reviewed_at?: string | null;
  review_note?: string | null;
  citing_paper_date?: string | null;
  citing_paper_journal?: string | null;
}

export interface NumericalDelta {
  metric: string;
  preprint_value: number;
  published_value: number;
  absolute_delta: number;
  relative_delta: number;
}

export interface EvidenceQuote {
  doc_id: string;
  section?: string | null;
  quote: string;
}

export interface ClaimDiff {
  diff_type: DiffType | string;
  preprint_claim_id?: string | null;
  published_claim_id?: string | null;
  preprint_text: string;
  published_text: string;
  change_description: string;
  numerical_delta?: NumericalDelta;
  // new fields
  materiality?: number | null;
  severity_tier?: FulltextTier | null;
  root_cause?: RootCause | string | null;
  rationale?: string | null;
  evidence?: EvidenceQuote[];
  label_problems?: string[] | null;
}

export type DriftEvent = DriftEventSummary & {
  claim_diffs: ClaimDiff[];
};

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
  // Always the project test inbox — notices never go to real authors.
  recipient_email: string;
  subject: string;
  body: string;
  status: NotificationStatus;
  drafted_at?: string | null;
  sent_at: string | null;
  error_message: string | null;
  // new fields
  citing_work_id?: string | null;
  cites?: CitesClass | null;
  intended_for?: {
    citing_paper_title?: string | null;
    citing_paper_doi?: string | null;
    authors?: string[] | null;
    relayed_by?: string | null;
  } | null;
  reviewer?: string | null;
  approved_at?: string | null;
  delivery?: "gmail" | "draft_only" | null;
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
  review_pending_total: number;
  superseded_citations_total: number;
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

// ── Citation analysis runs (GET /api/drift-events/<id>/citation-runs) ───────
export interface CitationRun {
  run_id: string;
  kind: "initial" | "incremental";
  status: "queued" | "running" | "done" | "failed";
  queued_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  since?: string | null;
  checked_until?: string | null;
  n_candidates?: number | null;
  n_judged?: number | null;
  n_unjudged?: number | null;
  coverage_complete?: boolean | null;
  // what the run could and could not examine (claimdrift Orchestra.coverage); notes are plain sentences
  coverage?: {
    europe_pmc_hits?: number; retrieved?: number; no_open_full_text?: number; matching_papers?: number | null;
    screened?: number | null; screen_capped?: number; download_failed?: number; candidates?: number;
    judged?: number; unjudged?: number; truncated?: boolean;
  } | null;
  coverage_notes?: string[] | null;
  steps?: number | null;
  step_at?: string | null;
  // live progress, rewritten after every step of a running run (claimdrift citations.runner.progress)
  progress?: {
    phase?: "prescreen" | "orchestrate" | "overflow" | "verify" | "done";
    screened?: number; to_screen?: number; candidates?: number; judged?: number; orchestrator_turns?: number;
    line?: string;
  } | null;
  unjudged?: { work_id: string; flags?: string[] | null; source?: string | null }[];
  judged_by?: { worker?: number; batch_overflow?: number } | null;
  counts?: Partial<Record<CitesClass, number>> | null;
  summary?: string | null;
  error?: string | null;
}

// ── Review queue (P1.8) ──────────────────────────────────────────────────────
export interface ReviewEventRow {
  kind: "event";
  id: string;
  event_id: string;
  paper_id?: string | null;
  preprint_doi?: string | null;
  published_doi?: string | null;
  title?: string | null;
  review_status?: ReviewStatus | null;
  review_reasons: string[];
  fulltext_tier?: FulltextTier | null;
  abstract_class?: AbstractSeverityClass | null;
  verification?: EventVerification | null;
  detected_at?: string | null;
  reviewer?: string | null;
  reviewed_at?: string | null;
}

export interface ReviewCitationRow {
  kind: "citation";
  id: string;
  drift_event_id: string;
  work_id?: string | null;
  title?: string | null;
  cites?: CitesClass | null;
  role?: CitationRole | null;
  notify_priority?: NotifyPriority | null;
  sentence?: string | null;
  sentence_verified?: boolean | null;
  flags: string[];
  found_via?: string | null;
  review_status?: ReviewStatus | null;
  review_reasons: string[];
  reviewer?: string | null;
  reviewed_at?: string | null;
}

export interface ReviewQueue {
  status: ReviewStatus;
  events?: ReviewEventRow[];
  citations?: ReviewCitationRow[];
}

export interface ReviewEventDetail {
  event: DriftEvent;
  citation_runs: CitationRun[];
  citations: ReviewCitationRow[];
}

export interface ReviewCitationDetail {
  citation: AffectedCitation;
  event: ReviewEventRow | null;
  claim_diff: ClaimDiff | null;
  drift_summary: string | null;
}

export type ReviewDecision = "approved" | "rejected";

export interface CitationCorrection {
  cites?: CitesClass;
  role?: CitationRole;
}

/** Human classification of a drift event; the machine's values are kept server-side in machine_verdict. */
export interface EventCorrection {
  abstract_class?: string;
  fulltext_tier?: string;
  claim_diffs?: { idx: number; root_cause?: string; severity_tier?: string }[];
}

export interface ReviewDecisionRequest {
  decision: ReviewDecision;
  reviewer?: string;
  note?: string;
  corrected?: CitationCorrection | EventCorrection;
}

export interface ReviewDecisionResponse {
  review_status: ReviewStatus;
  reviewer: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  corrected_fields?: string[] | null;
  machine_verdict?: Record<string, string | null> | null;
  cites?: CitesClass | null;
  role?: CitationRole | null;
  abstract_class?: string | null;
  fulltext_tier?: string | null;
}

// ── Author self-check (P1.10) ────────────────────────────────────────────────
export interface SelfcheckChange {
  preprint_text: string;
  published_text: string;
  change_description?: string | null;
  severity_tier?: FulltextTier | null;
  root_cause?: string | null;
}

export type SelfcheckRefStatus = "drift_found" | "not_in_library" | "not_tracked" | "no_match" | "skipped";

export type PrecheckStatus =
  | "ready"
  | "doi_not_found"
  | "v1_unavailable"
  | "not_published_yet"
  | "no_open_full_text"
  | "check_failed";

export interface SelfcheckPrecheck {
  status: PrecheckStatus;
  detail?: string | null;
  title?: string | null;
  published_doi?: string | null;
  versions?: string[] | null;
}

export interface SelfcheckReferenceResult {
  line: string;
  doi: string | null;
  status: SelfcheckRefStatus;
  matched_by: "doi" | "title" | null;
  detail?: string | null;
  title_coverage?: number | null;
  title_field?: "preprint_title" | "published_title" | null;
  needs_confirmation?: boolean;
  precheck?: SelfcheckPrecheck | null;
  suggestions?: {
    title_field: "preprint_title" | "published_title";
    title_coverage: number;
    event: NonNullable<SelfcheckReferenceResult["event"]>;
  }[];
  event?: {
    event_id: string;
    preprint_doi: string;
    published_doi: string;
    preprint_title?: string | null;
    published_title?: string | null;
    drift_summary?: string | null;
    fulltext_tier?: FulltextTier | null;
    abstract_class?: AbstractSeverityClass | null;
    review_status?: ReviewStatus | null;
    changes: SelfcheckChange[];
  };
}

export type OnDemandStatus = "running" | "done" | "failed" | "unknown" | "not_analysable";

export interface SelfcheckReferencesResponse {
  n_lines: number;
  truncated?: boolean;
  n_drift_found: number;
  not_in_library: string[];
  not_in_library_status: Record<string, OnDemandStatus | null>;
  results: SelfcheckReferenceResult[];
  summary?: {
    drift_found: number;
    significant_or_major: number;
    needs_confirmation: number;
    not_in_library: number;
    not_tracked: number;
    no_match: number;
    skipped: number;
  };
}

export interface SelfcheckAnalyzeResponse {
  preprint_doi: string;
  status: OnDemandStatus;
  event_id?: string;
  error?: string;
  precheck?: SelfcheckPrecheck | null;
  updated_at?: string;
  // live progress of the on-demand analysis (apps/bff/review_api._run_ondemand); t = seconds since start
  progress?: {
    line?: string;
    steps_done?: number;
    steps_total?: number;
    log?: { t: number; line: string; done: boolean }[];
  } | null;
}

export interface SelfcheckPublishedResult {
  event: NonNullable<SelfcheckReferenceResult["event"]>;
  status: "judged" | "not_judged" | "no_traceable_value";
  detail?: string;
  old?: string;
  new?: string;
  cites?: string | null;
  role?: string | null;
  sentence?: string;
  sentence_verified?: boolean;
  relayed_by?: string;
  reason?: string;
}

export interface SelfcheckPublishedResponse {
  paper: { work_id: string | null; title: string | null; doi: string | null; authors?: string; detail?: string };
  status: "checked" | "not_found" | "no_full_text";
  n_library_preprints_cited?: number;
  results: SelfcheckPublishedResult[];
  summary?: { relies_on_old_value: number; judged: number };
}

export type ValueVerdict =
  | "uses_old_value"
  | "uses_current_value"
  | "mentions_both"
  | "unchanged_value"
  | "cannot_tell";

export type SearchMode = "hybrid" | "elser" | "bm25";

export interface SelfcheckSentenceMatch {
  event_id: string;
  paper_id?: string | null;
  preprint_doi: string;
  published_doi: string;
  claim_diff_idx?: number | null;
  preprint_text: string;
  published_text: string;
  change_description?: string | null;
  severity_tier?: FulltextTier | null;
  root_cause?: string | null;
  score?: number | null;
  elser_score?: number | null;
  bm25_score?: number | null;
  strength?: "strong" | "possible" | "weak";
  /** program hints passed to the judge: author named | DOI cited | a value of only one version | a value kept in both */
  evidence?: Array<"author" | "doi" | "value" | "shared_value">;
  /** the judge's one-line reason; strength strong/possible/weak = judged yes/possible/no */
  judge_reason?: string | null;
  retrieval_rank?: number | null;
  value_check?: {
    verdict: ValueVerdict;
    old_values_found: string[];
    new_values_found: string[];
  } | null;
}

export interface SelfcheckSentenceResult {
  sentence: string;
  mode: SearchMode;
  truncated?: boolean;
  match?: "strong" | "possible" | "no_match";
  matches: SelfcheckSentenceMatch[];
  weak_matches?: SelfcheckSentenceMatch[];
  /** "unavailable" = the model call failed; matches are then unconfirmed retrieval candidates */
  judge?: "flash" | "unavailable";
  n_candidates?: number;
}

export interface SelfcheckSentencesResponse {
  results: SelfcheckSentenceResult[];
  n_submitted?: number;
  n_checked?: number;
  n_ignored?: number;
  summary?: { uses_old_value: number; matched: number; no_match: number };
}
