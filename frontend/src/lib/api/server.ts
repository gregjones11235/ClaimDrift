// BFF reads for SERVER components (pages and layouts). They forward the visitor's session cookie, so the BFF sees
// the logged-in user and applies its role checks; a 401 sends the visitor to /login. Browser-side calls (forms,
// polling) live in client.ts and go through the frontend's own /api/* rewrite instead.
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
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
  SessionUser,
} from "@/types/claimdrift";
import { toBffError } from "./client";

// Server-to-server address of the BFF (the browser never uses it). BFF_INTERNAL_URL wins when set.
export const BFF_SERVER_URL =
  process.env.BFF_INTERNAL_URL ?? process.env.NEXT_PUBLIC_BFF_URL ?? "http://127.0.0.1:8787";

// All views are server-rendered, so each navigation re-fetches from the BFF.
// `no-store` made every dashboard↔detail↔live switch re-query Elasticsearch
// end-to-end, which is the dominant source of perceived slowness. A 30s
// incremental cache lets rapid back-and-forth navigation reuse the last
// response (instant), while still refreshing in the background so the numbers
// stay accurate within half a minute. The cache key includes the request
// headers, so each session (cookie) has its own entries.
const REVALIDATE_SECONDS = 30;

async function authHeaders(): Promise<Record<string, string>> {
  const jar = await cookies();
  const cookie = jar.toString();
  return cookie ? { cookie } : {};
}

async function bffGet<T>(path: string, errorLabel: string, fresh: boolean): Promise<T> {
  const res = await fetch(`${BFF_SERVER_URL}${path}`, {
    headers: await authHeaders(),
    ...(fresh ? { cache: "no-store" as const } : { next: { revalidate: REVALIDATE_SECONDS } }),
  });
  if (res.status === 401) redirect("/login");
  if (!res.ok) throw await toBffError(res, errorLabel);
  return res.json();
}

const fetchJson = <T>(path: string, label: string) => bffGet<T>(path, label, false);
// Review state changes when a reviewer decides, so these reads are never cached.
const fetchFresh = <T>(path: string, label: string) => bffGet<T>(path, label, true);

// The logged-in user, or null (no / expired session). Never cached.
export async function getMe(): Promise<SessionUser | null> {
  const headers = await authHeaders();
  if (!headers.cookie) return null;
  const res = await fetch(`${BFF_SERVER_URL}/api/auth/me`, { headers, cache: "no-store" });
  if (!res.ok) return null;
  return ((await res.json()) as { user: SessionUser }).user;
}

// For layouts: the logged-in user, or a redirect to /login.
export async function requireUser(): Promise<SessionUser> {
  const user = await getMe();
  if (!user) redirect("/login");
  return user;
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

// Citation-analysis runs for an event.
export function getCitationRuns(id: string): Promise<{ items: CitationRun[]; count: number }> {
  return fetchFresh(`/api/drift-events/${id}/citation-runs`, "citation runs");
}

// Whole-index dashboard rollups (computed server-side via ES aggregations).
// Use these for the summary cards instead of summing the (capped) /api/drift-events
// page client-side — that page is limited to the most-recent 100 events.
export function getStats(): Promise<DashboardStats> {
  return fetchJson("/api/stats", "stats");
}

// ── Review queue (P1.8, operator only: the BFF answers 403 to a customer) ────
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
