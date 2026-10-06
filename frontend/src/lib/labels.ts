// Human-readable labels and colours for the redesigned backend's enums.
// Shared by the event, citation, review and self-check views so every page
// names the same code the same way.

// Two separate severity dimensions — never merged into one number.
export const ABSTRACT_SCALE_LABEL = "Abstract-level (Brierley 3 classes)";
export const FULLTEXT_SCALE_LABEL = "Full-text (4 tiers)";

export const ABSTRACT_CLASS_LABEL: Record<string, string> = {
  no_change: "No change",
  minor: "Minor",
  major: "Major",
};
export const ABSTRACT_CLASS_COLOR: Record<string, string> = {
  no_change: "var(--grn)",
  minor: "var(--y)",
  major: "var(--rd)",
};

export const FULLTEXT_TIERS = ["minor", "medium", "significant", "major"] as const;
export const FULLTEXT_TIER_COLOR: Record<string, string> = {
  minor: "var(--grn)",
  medium: "var(--y)",
  significant: "var(--or)",
  major: "var(--rd)",
};

export const ROOT_CAUSE_LABEL: Record<string, string> = {
  definition_change: "Definition change",
  analysis_removed: "Analysis removed",
  reporting_choice: "Reporting choice",
  model_respecified: "Model re-specified",
  outcome_redefinition: "Outcome redefined",
  method_change: "Method change",
  generality_reversed: "Generality reversed",
  data_revision: "Data revision",
  wording_only: "Wording only",
};

export const REVIEW_STATUS_LABEL: Record<string, string> = {
  pending: "Pending review",
  approved: "Approved",
  rejected: "Rejected",
  not_required: "No review needed",
};
export const REVIEW_STATUS_COLOR: Record<string, string> = {
  pending: "var(--y)",
  approved: "var(--grn)",
  rejected: "var(--rd)",
  not_required: "var(--gr)",
};

// Customer-facing view of review_status: what the reader can rely on, not a task for them. A rejected item never
// reaches a customer view (the BFF hides it).
export const VERIFICATION_LABEL: Record<string, string> = {
  not_required: "Auto-verified",
  approved: "Human-confirmed",
  pending: "Provisional",
};
export const VERIFICATION_COLOR: Record<string, string> = {
  not_required: "var(--gr)",
  approved: "var(--grn)",
  pending: "var(--y)",
};
export const VERIFICATION_EXPLANATION: Record<string, string> = {
  not_required: "Every automatic check passed (evidence quotes found verbatim, severities consistent).",
  approved: "Confirmed by the ClaimDrift team.",
  pending: "An automatic check did not pass; the ClaimDrift team is confirming this finding.",
};

export const REVIEW_REASON_LABEL: Record<string, string> = {
  quote_unverified: "Evidence quote not verified",
  severity_mismatch: "Abstract vs full-text severity disagree",
  high_severity_notify: "High severity with papers to notify",
  withdrawn_version: "Preprint has a withdrawn version",
  reanalyzed_after_review: "Re-analysed after an earlier review",
  unclear: "Citation analysis could not decide",
  outgoing_notification: "Would send a notification",
};

export const CITES_LABEL: Record<string, string> = {
  superseded: "Uses superseded value",
  current: "Uses current value",
  flagged_as_previous: "Flags it as the earlier value",
  indirect: "Indirect (via another paper)",
  not_relying: "Not relying on it",
  unclear: "Unclear",
};
export const CITES_COLOR: Record<string, string> = {
  superseded: "var(--rd)",
  indirect: "var(--or)",
  unclear: "var(--y)",
  flagged_as_previous: "var(--bl)",
  current: "var(--grn)",
  not_relying: "var(--gr)",
};
export const CITES_ORDER = [
  "superseded",
  "indirect",
  "unclear",
  "flagged_as_previous",
  "current",
  "not_relying",
] as const;

export const ROLE_LABEL: Record<string, string> = {
  model_input: "Model input",
  reported_as_fact: "Reported as fact",
  background: "Background",
  unknown: "Unknown role",
};
export const ROLE_ORDER = ["model_input", "reported_as_fact", "background", "unknown"] as const;

export const FOUND_VIA_LABEL: Record<string, string> = {
  prefetch: "Pre-screen",
  search_more: "Extra search",
  chain: "Citation chain",
  semantic: "Semantic search",
};

export const FLAG_EXPLANATION: Record<string, string> = {
  F1: "Old value is not next to a sentence citing the target",
  F2: "Old value appears only in a table or list",
  F3: "Old and current value appear in the same sentence",
};

export const UNCLEAR_RULE_EXPLANATION: Record<string, string> = {
  ...FLAG_EXPLANATION,
  M1: "Cannot tell whether the value is the target's finding",
  M2: "Cannot tell whether the value is a model input or only mentioned",
};

export const NOTIFY_PRIORITY_COLOR: Record<string, string> = {
  high: "var(--rd)",
  normal: "var(--y)",
  low: "var(--gr)",
};

export const VERIFICATION_STATUS_COLOR: Record<string, string> = {
  verified: "var(--grn)",
  partial: "var(--y)",
  unsupported: "var(--rd)",
};

export const CITATION_ANALYSIS_LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
  no_target: "No numeric target to search for",
  not_queued: "Not queued",
};

export const TEST_INBOX = "claimdriftnotifier@gmail.com";

export function labelOf(map: Record<string, string>, code: string | null | undefined): string {
  if (!code) return "—";
  return map[code] ?? code;
}

export function shortDate(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "—";
}

export function shortDateTime(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}
