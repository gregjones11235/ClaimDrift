// BFF calls made from the BROWSER (client components: forms, polling). They use relative /api/* URLs, which the
// frontend rewrites to the BFF (next.config.ts), so the session cookie set on the frontend's domain is sent along.
// Server components read through server.ts instead.
import { unstable_rethrow } from "next/navigation";
import {
  ReviewDecisionRequest,
  ReviewDecisionResponse,
  SelfcheckReferencesResponse,
  SelfcheckAnalyzeResponse,
  SelfcheckSentencesResponse,
  SearchMode,
  SelfcheckPublishedResponse,
  SessionUser,
  DriftEventSummary,
} from "@/types/claimdrift";

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

export async function toBffError(res: Response, errorLabel: string): Promise<BffError> {
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
  // A redirect (e.g. to /login after a 401) thrown inside a page's try/catch must keep propagating.
  unstable_rethrow(e);
  if (e instanceof BffError && e.code === "local_es_required") {
    return "This feature needs the local Elasticsearch: the BFF is running without CLAIMDRIFT_ES (local_es_required).";
  }
  if (e instanceof Error) return e.message;
  return String(e);
}

async function fetchFresh<T>(path: string, errorLabel: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw await toBffError(res, errorLabel);
  return res.json();
}

async function postJson<T>(path: string, body: unknown, errorLabel: string): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) throw await toBffError(res, errorLabel);
  return res.json();
}

// Browser-side event list (the live stream page picks an event to follow).
export function getDriftEvents(): Promise<{ items: DriftEventSummary[]; count: number }> {
  return fetchFresh("/api/drift-events", "drift events");
}

// ── Accounts ─────────────────────────────────────────────────────────────────
export function postLogin(email: string, password: string): Promise<{ user: SessionUser }> {
  return postJson("/api/auth/login", { email, password }, "login");
}

export function postRegister(email: string, password: string, name: string): Promise<{ user: SessionUser }> {
  return postJson("/api/auth/register", { email, password, name }, "registration");
}

export function postLogout(): Promise<{ ok: boolean }> {
  return postJson("/api/auth/logout", {}, "logout");
}

// ── Review decision (operator only) ──────────────────────────────────────────
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
