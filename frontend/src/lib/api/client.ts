import {
  DriftEventSummary,
  DriftEvent,
  AffectedCitation,
  NotificationLog,
  DriftPattern,
  DashboardStats,
  Claim
} from "@/types/claimdrift";

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL ?? "http://127.0.0.1:8787";

// All views are server-rendered, so each navigation re-fetches from the BFF.
// `no-store` made every dashboard↔detail↔live switch re-query Elasticsearch
// end-to-end, which is the dominant source of perceived slowness. A 30s
// incremental cache lets rapid back-and-forth navigation reuse the last
// response (instant), while still refreshing in the background so the numbers
// stay accurate within half a minute. The live SSE stream is a separate
// EventSource and is unaffected by this — it stays truly real-time.
const REVALIDATE_SECONDS = 30;

async function fetchJson<T>(path: string, errorLabel: string): Promise<T> {
  const res = await fetch(`${BFF_URL}${path}`, {
    next: { revalidate: REVALIDATE_SECONDS },
  });
  if (!res.ok) throw new Error(`Failed to fetch ${errorLabel}`);
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

export function getPatterns(): Promise<{ items: DriftPattern[]; count: number }> {
  return fetchJson("/api/patterns", "patterns");
}

// Whole-index dashboard rollups (computed server-side via ES aggregations).
// Use these for the summary cards instead of summing the (capped) /api/drift-events
// page client-side — that page is limited to the most-recent 100 events.
export function getStats(): Promise<DashboardStats> {
  return fetchJson("/api/stats", "stats");
}
