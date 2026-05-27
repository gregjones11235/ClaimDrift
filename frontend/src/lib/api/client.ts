import { 
  DriftEventSummary, 
  DriftEvent, 
  AffectedCitation, 
  NotificationLog, 
  DriftPattern, 
  Claim 
} from "@/types/claimdrift";

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL ?? "http://127.0.0.1:8787";

export async function getDriftEvents(): Promise<{ items: DriftEventSummary[]; count: number }> {
  const res = await fetch(`${BFF_URL}/api/drift-events`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch drift events");
  return res.json();
}

export async function getDriftEvent(id: string): Promise<DriftEvent> {
  const res = await fetch(`${BFF_URL}/api/drift-events/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch drift event");
  return res.json();
}

export async function getClaims(id: string): Promise<{ items: Claim[]; count: number }> {
  const res = await fetch(`${BFF_URL}/api/drift-events/${id}/claims`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch claims");
  return res.json();
}

export async function getAffectedCitations(id: string): Promise<{ items: AffectedCitation[]; count: number }> {
  const res = await fetch(`${BFF_URL}/api/drift-events/${id}/affected-citations`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch affected citations");
  return res.json();
}

export async function getNotifications(id: string): Promise<{ items: NotificationLog[]; count: number }> {
  const res = await fetch(`${BFF_URL}/api/drift-events/${id}/notifications`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch notifications");
  return res.json();
}

export async function getPatterns(): Promise<{ items: DriftPattern[]; count: number }> {
  const res = await fetch(`${BFF_URL}/api/patterns`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch patterns");
  return res.json();
}
