import { getAffectedCitations, getCitationRuns, getDriftEvent, bffErrorMessage } from "@/lib/api/client";
import { CitationAnalysis, CitationRun } from "@/types/claimdrift";
import { CitationList } from "@/components/features/CitationList";
import { CitationRunsPanel } from "@/components/features/CitationRunsPanel";
import Link from "next/link";

export default async function CitationsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [{ items: citations }, event] = await Promise.all([
    getAffectedCitations(id),
    getDriftEvent(id).catch(() => null),
  ]);

  // Runs need the local ES on the BFF; old demo events have none.
  let runs: CitationRun[] | null = null;
  let runsError: string | null = null;
  try {
    runs = (await getCitationRuns(id)).items;
  } catch (e) {
    runsError = bffErrorMessage(e);
  }
  const analysis: CitationAnalysis | null = event?.citation_analysis ?? null;

  return (
    <div style={{ width: "100%" }}>
      <Link href={`/event/${id}`} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--mono)", fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--gr)", textDecoration: "none", marginBottom: 16 }}>
        ← Drift Detail
      </Link>
      <CitationRunsPanel analysis={analysis} runs={runs} runsError={analysis ? runsError : null} />
      <CitationList citations={citations} />
    </div>
  );
}
