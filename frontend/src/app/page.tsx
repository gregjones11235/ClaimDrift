import { getDriftEvents, getAffectedCitations, getNotifications, getPatterns } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import { CornerDownRight, ArrowRight } from "lucide-react";
import Link from "next/link";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

export default async function DashboardPage() {
  const [{ items: events }, { items: patterns }] = await Promise.all([
    getDriftEvents(),
    getPatterns(),
  ]);

  if (events.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-neutral-400 italic font-sans text-[13px]">
        No drift events yet. Run the pipeline to begin.
      </div>
    );
  }

  // Roll up affected_citations + notifications across all events in parallel so
  // the summary cards stay in sync with the underlying ES state.
  const perEvent = await Promise.all(
    events.map(async (e) => {
      const [ac, nl] = await Promise.all([
        getAffectedCitations(e.event_id),
        getNotifications(e.event_id),
      ]);
      return { event_id: e.event_id, citations: ac.items, notifications: nl.items };
    })
  );

  const totalAffectedCitations = perEvent.reduce((acc, p) => acc + p.citations.length, 0);
  const sentNotifications = perEvent.reduce(
    (acc, p) => acc + p.notifications.filter((n) => n.status === "sent").length,
    0
  );
  const totalNotifications = perEvent.reduce((acc, p) => acc + p.notifications.length, 0);

  const highSeverityCount = events.filter((e) => e.materiality_score >= 0.7).length;
  const avgScore = events.reduce((acc, e) => acc + e.materiality_score, 0) / events.length;

  // Surface the most common pattern_type so the summary card reflects the
  // actual memory state rather than a hand-picked label.
  const patternTypeCounts = patterns.reduce<Record<string, number>>((acc, p) => {
    if (p.pattern_type) acc[p.pattern_type] = (acc[p.pattern_type] ?? 0) + 1;
    return acc;
  }, {});
  const topPatternType = Object.entries(patternTypeCounts)
    .sort((a, b) => b[1] - a[1])[0]?.[0];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-white border border-black p-4">
          <div className="text-[12px] text-black mb-1 font-sans">Tracked events</div>
          <div className="text-2xl font-medium font-sans text-black">{events.length}</div>
          <div className="text-[11px] font-mono text-[#666] mt-1">
            {highSeverityCount} high severity (≥0.7)
          </div>
        </div>
        <div className="bg-white border border-black p-4">
          <div className="text-[12px] text-black mb-1 font-sans">Avg materiality_score</div>
          <div className="text-2xl font-medium font-sans text-black">{avgScore.toFixed(2)}</div>
          <div className="text-[11px] font-mono text-[#666] mt-1">across {events.length} events</div>
        </div>
        <div className="bg-white border border-black p-4">
          <div className="text-[12px] text-black mb-1 font-sans">Affected citations</div>
          <div className="text-2xl font-medium font-sans text-black">{totalAffectedCitations}</div>
          <div className="text-[11px] font-mono text-[#666] mt-1">
            {sentNotifications}/{totalNotifications} emails sent
          </div>
        </div>
        <div className="bg-white border border-black p-4">
          <div className="text-[12px] text-black mb-1 font-sans">Patterns learned</div>
          <div className="text-2xl font-medium font-sans text-black">{patterns.length}</div>
          <div className="text-[11px] font-mono text-[#666] mt-1">
            {topPatternType ?? "—"}
          </div>
        </div>
      </div>

      <div className="border border-black bg-white overflow-hidden">
        <table className="w-full text-left border-collapse table-fixed">
          <thead className="bg-white border-b border-black">
            <tr>
              <th className="font-sans font-medium p-4 w-[32%] text-[10px] uppercase tracking-wider text-[#666]">drift_summary</th>
              <th className="font-sans font-medium p-4 w-[24%] text-[10px] uppercase tracking-wider text-[#666]">
                preprint_doi &rarr; published_doi
              </th>
              <th className="font-sans font-medium p-4 w-[14%] text-[10px] uppercase tracking-wider text-[#666]">materiality_score</th>
              <th className="font-sans font-medium p-4 w-[14%] text-[10px] uppercase tracking-wider text-[#666]">diff_type</th>
              <th className="font-sans font-medium p-4 w-[10%] text-[10px] uppercase tracking-wider text-[#666]">detected_at</th>
              <th className="font-medium p-4 w-[6%] text-[10px] uppercase tracking-wider text-[#666]"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-black">
            {events.map((event) => {
              const diffType = event.claim_diffs?.[0]?.diff_type ?? "—";
              const scoreColor = event.materiality_score >= 0.7
                ? "text-[#C92A2A]"
                : event.materiality_score >= 0.5
                ? "text-[#B45309]"
                : "text-[#0D7A5F]";
              return (
                <tr
                  key={event.event_id}
                  className="hover:bg-[#F5F5F5] transition-colors group relative"
                >
                  <td className="p-4 align-top text-[12px] text-black font-sans pr-4 leading-[1.4]">
                    <div className="line-clamp-2">{event.drift_summary}</div>
                  </td>
                  <td className="p-4 align-top">
                    <Link href={`/event/${event.event_id}`} className="absolute inset-0 z-10" />
                    <div className="text-[11px] font-mono text-black truncate" title={event.preprint_doi}>
                      {event.preprint_doi}
                    </div>
                    <div className="flex items-center gap-1.5 mt-1 text-[#666]">
                      <CornerDownRight className="w-3 h-3 flex-shrink-0" />
                      <div className="text-[11px] font-mono truncate" title={event.published_doi}>
                        {event.published_doi}
                      </div>
                    </div>
                  </td>
                  <td className={`p-4 align-top font-mono font-medium text-[13px] ${scoreColor}`}>
                    {event.materiality_score.toFixed(2)}
                  </td>
                  <td className="p-4 align-top">
                    <Badge variant="outline" className="text-[10px] px-2 py-0.5 font-sans bg-white text-black border-black rounded-none">
                      {diffType}
                    </Badge>
                  </td>
                  <td className="p-4 align-top text-[12px] text-black font-mono">
                    {dayjs(event.detected_at).format("YYYY-MM-DD")}
                  </td>
                  <td className="p-4 align-top">
                    <div className="inline-flex items-center gap-1 text-[12px] font-medium text-black group-hover:underline">
                      View <ArrowRight className="w-3 h-3" />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
