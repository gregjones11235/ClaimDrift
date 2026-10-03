import {
  DriftEventSummary,
  DriftEvent,
  AffectedCitation,
  NotificationLog,
  DashboardStats,
  Claim,
  CitationRun,
  ReviewQueue,
  ReviewEventDetail,
  ReviewCitationDetail,
  ReviewDecisionRequest,
  ReviewDecisionResponse,
  SelfcheckReferencesResponse,
  SelfcheckAnalyzeResponse,
  SelfcheckSentencesResponse,
  SearchMode,
  SelfcheckPublishedResponse,
} from "@/types/claimdrift";

export const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL ?? "http://127.0.0.1:8787";

// All views are server-rendered, so each navigation re-fetches from the BFF.
// `no-store` made every dashboard↔detail↔live switch re-query Elasticsearch
// end-to-end, which is the dominant source of perceived slowness. A 30s
// incremental cache lets rapid back-and-forth navigation reuse the last
// response (instant), while still refreshing in the background so the numbers
// stay accurate within half a minute. The live SSE stream is a separate
// EventSource and is unaffected by this — it stays truly real-time.
const REVALIDATE_SECONDS = 30;

// Error carrying the BFF's {error, message} body. The review and self-check
// routes answer 503 {error: "local_es_required"} when the BFF runs without the
// local Elasticsearch; pages show that message instead of crashing.
export class BffError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message || code);
    this.status = status;
    this.code = code;
  }
}

async function toBffError(res: Response, errorLabel: string): Promise<BffError> {
  let code = `http_${res.status}`;
  let message = `Failed to fetch ${errorLabel}`;
  try {
    const body = (await res.json()) as { error?: string; message?: string };
    if (body.error) code = body.error;
    if (body.message) message = body.message;
    else if (body.error) message = `${errorLabel}: ${body.error}`;
  } catch {
    /* non-JSON error body */
  }
  return new BffError(res.status, code, message);
}

// Human-readable text for any error thrown by the functions below.
export function bffErrorMessage(e: unknown): string {
  if (e instanceof BffError && e.code === "local_es_required") {
    return "This feature needs the local Elasticsearch: the BFF is running without CLAIMDRIFT_ES (local_es_required).";
  }
  if (e instanceof Error) return e.message;
  return String(e);
}

async function fetchJson<T>(path: string, errorLabel: string): Promise<T> {
  const res = await fetch(`${BFF_URL}${path}`, {
    next: { revalidate: REVALIDATE_SECONDS },
  });
  if (!res.ok) throw new Error(`Failed to fetch ${errorLabel}`);
  return res.json();
}

// Review state changes when a reviewer decides, so these reads are never cached.
async function fetchFresh<T>(path: string, errorLabel: string): Promise<T> {
  const res = await fetch(`${BFF_URL}${path}`, { cache: "no-store" });
  if (!res.ok) throw await toBffError(res, errorLabel);
  return res.json();
}

async function postJson<T>(path: string, body: unknown, errorLabel: string): Promise<T> {
  const res = await fetch(`${BFF_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) throw await toBffError(res, errorLabel);
  return res.json();
}

export function getDriftEvents(): Promise<{ items: DriftEventSummary[]; count: number }> {
  return fetchJson("/api/drift-events", "drift events");
}

export function getDriftEvent(id: string): Promise<DriftEvent> {
  return fetchJson(`/api/drift-events/${id}`, "drift event");
}

export function getClaims(id: string): Promise<{ items: Claim[]; count: number }> {
  return fetchJson(`/api/drift-events/${id}/claims`, "claims");
}

export function getAffectedCitations(id: string): Promise<{ items: AffectedCitation[]; count: number }> {
  return fetchJson(`/api/drift-events/${id}/affected-citations`, "affected citations");
}

export function getNotifications(id: string): Promise<{ items: NotificationLog[]; count: number }> {
  return fetchJson(`/api/drift-events/${id}/notifications`, "notifications");
}

// Citation-analysis runs for an event (needs the local ES on the BFF).
export function getCitationRuns(id: string): Promise<{ items: CitationRun[]; count: number }> {
  return fetchFresh(`/api/drift-events/${id}/citation-runs`, "citation runs");
}

// Whole-index dashboard rollups (computed server-side via ES aggregations).
// Use these for the summary cards instead of summing the (capped) /api/drift-events
// page client-side — that page is limited to the most-recent 100 events.
export function getStats(): Promise<DashboardStats> {
  return fetchJson("/api/stats", "stats");
}

// ── Review queue (P1.8) ──────────────────────────────────────────────────────
export function getReviewQueue(
  kind: "all" | "events" | "citations",
  status: string,
): Promise<ReviewQueue> {
  const q = new URLSearchParams({ kind, status });
  return fetchFresh(`/api/review-queue?${q}`, "review queue");
}

export function getReviewEvent(id: string): Promise<ReviewEventDetail> {
  return fetchFresh(`/api/review/events/${encodeURIComponent(id)}`, "review event");
}

export function getReviewCitation(id: string): Promise<ReviewCitationDetail> {
  return fetchFresh(`/api/review/citations/${encodeURIComponent(id)}`, "review citation");
}

export function postReviewDecision(
  kind: "events" | "citations",
  id: string,
  body: ReviewDecisionRequest,
): Promise<ReviewDecisionResponse> {
  return postJson(`/api/review/${kind}/${encodeURIComponent(id)}`, body, "review decision");
}

// ── Author self-check (P1.10) ────────────────────────────────────────────────
export function postSelfcheckReferences(text: string): Promise<SelfcheckReferencesResponse> {
  return postJson("/api/selfcheck/references", { text }, "reference check");
}

export function postSelfcheckPublished(paper: string): Promise<SelfcheckPublishedResponse> {
  return postJson("/api/selfcheck/published", { paper }, "published-paper check");
}

export function postSelfcheckSentences(
  sentences: string[],
  mode: SearchMode,
): Promise<SelfcheckSentencesResponse> {
  return postJson("/api/selfcheck/sentences", { sentences, mode }, "sentence check");
}

export function postSelfcheckAnalyze(preprintDoi: string): Promise<SelfcheckAnalyzeResponse> {
  return postJson("/api/selfcheck/analyze", { preprint_doi: preprintDoi }, "on-demand analysis");
}

export function getSelfcheckAnalyze(doi: string): Promise<SelfcheckAnalyzeResponse> {
  return fetchFresh(`/api/selfcheck/analyze?doi=${encodeURIComponent(doi)}`, "on-demand analysis status");
}
