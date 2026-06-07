import { getAffectedCitations } from "@/lib/api/client";
import { CitationList } from "@/components/features/CitationList";
import Link from "next/link";

export default async function CitationsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { items: citations, count } = await getAffectedCitations(id);

  return (
    <div style={{ width: "100%" }}>
      <Link href={`/event/${id}`} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--mono)", fontSize: 9, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--gr)", textDecoration: "none", marginBottom: 16 }}>
        ← Drift Detail
      </Link>
      <CitationList citations={citations} eventId={id} />
    </div>
  );
}
