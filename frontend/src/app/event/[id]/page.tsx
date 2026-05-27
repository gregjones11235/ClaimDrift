import { getDriftEvent, getClaims } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import { ClaimDiffViewer } from "@/components/features/ClaimDiffViewer";
import { NumericalDeltaCard } from "@/components/features/NumericalDeltaCard";
import { ArrowLeft, ArrowRight, Download, FileDiff } from "lucide-react";
import Link from "next/link";
import dayjs from "dayjs";

export default async function DriftDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [event] = await Promise.all([
    getDriftEvent(id),
    getClaims(id)
  ]);

  const scoreColor = event.materiality_score >= 0.7 ? 'text-danger' : event.materiality_score >= 0.5 ? 'text-caution' : 'text-brand';
  
  // Calculate summary for right side of materiality bar
  const diffTypes = event.claim_diffs.map(d => d.diff_type);
  const numShifts = diffTypes.filter(t => t === 'numerical_shift').length;
  const hedgingAdded = diffTypes.some(t => t === 'hedging_added');
  
  const diffSummaryText = [
    numShifts > 0 ? `${numShifts} numerical_shift${numShifts > 1 ? 's' : ''}` : null,
    hedgingAdded ? 'hedging added' : null
  ].filter(Boolean).join(' · ') || `${event.claim_diffs.length} total diffs`;

  return (
    <div className="max-w-4xl">
      <Link href="/" className="inline-flex items-center gap-1.5 text-[13px] font-sans text-black hover:underline mb-6 transition-colors">
        <ArrowLeft className="w-3.5 h-3.5" /> Back to dashboard
      </Link>

      <div className="mb-6">
        <h1 className="text-[22px] font-medium font-sans text-black leading-snug flex items-center gap-2 mb-1">
          <FileDiff className="w-5 h-5" /> Drift detail
        </h1>
        <div className="text-[13px] font-mono text-[#666]">
          event_id: {event.event_id} &middot; preprint_doi: {event.preprint_doi}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mb-8 border-b border-black pb-4">
        <Badge variant="outline" className="font-mono text-[11px] px-2 py-0.5 border-black bg-white text-black rounded-none uppercase">
          {event.claim_diffs[0]?.diff_type || "multiple_diffs"}
        </Badge>
        <Badge variant="outline" className="font-mono text-[11px] px-2 py-0.5 border-black bg-white text-black rounded-none">
          {event.preprint_version_compared} &rarr; published
        </Badge>
        <Badge variant="outline" className="font-mono text-[11px] px-2 py-0.5 border-black bg-white text-black rounded-none">
          materiality_score: {event.materiality_score.toFixed(2)}
        </Badge>
        <Badge variant="outline" className="font-mono text-[11px] px-2 py-0.5 border-black bg-white text-black rounded-none">
          detected_at: {dayjs(event.detected_at).format('YYYY-MM-DD')}
        </Badge>
      </div>

      <div className="border border-black p-4 mb-8 bg-white">
        <div className="font-mono text-[10px] text-black mb-1.5 tracking-wide uppercase">
          drift_summary
        </div>
        <div className="font-sans text-[13px] leading-[1.6] text-black">
          "{event.drift_summary}"
        </div>
      </div>

      <div className="border border-black p-6 bg-white mb-8 flex items-center gap-8">
        <div className="flex flex-col">
          <div className="font-mono text-[32px] font-medium text-[#C92A2A] leading-none tracking-tight">
            {event.materiality_score.toFixed(2)}
          </div>
          <div className="font-mono text-[10px] text-[#666] mt-2 uppercase tracking-wide">
            materiality_score
          </div>
        </div>
        
        <div className="flex-1 relative">
          <div className="flex h-4 border border-black bg-white mb-2 relative">
            <div className="bg-[#E6F4F0] w-[33.33%] border-r border-black"></div>
            <div className="bg-[#FFF9DB] w-[33.33%] border-r border-black"></div>
            <div className="bg-[#FFF5F5] w-[33.34%]"></div>
            
            {/* Marker */}
            <div 
              className="absolute top-[-4px] bottom-[-4px] w-[3px] bg-black" 
              style={{ left: `${event.materiality_score * 100}%`, marginLeft: '-1.5px' }}
            />
          </div>
          <div className="flex justify-between font-mono text-[10px] text-[#666]">
            <span>0.0</span>
            <span>0.5</span>
            <span>1.0</span>
          </div>
        </div>
      </div>

      <div className="space-y-6">
        {event.claim_diffs.map((diff, idx) => (
          <div key={idx}>
            {diff.numerical_delta && (
              <NumericalDeltaCard delta={diff.numerical_delta} />
            )}
            <ClaimDiffViewer diff={diff} />
          </div>
        ))}
      </div>

      <div className="flex gap-3 mt-8">
        <button className="text-[13px] px-4 py-2 border border-black rounded-none font-sans font-medium text-black bg-white hover:bg-neutral-100 transition-colors">
          Mark reviewed
        </button>
        <button className="text-[13px] px-4 py-2 border border-black rounded-none font-sans font-medium text-black bg-white hover:bg-neutral-100 transition-colors flex items-center gap-1.5">
          <Download className="w-3.5 h-3.5" /> Export PDF
        </button>
        <Link href={`/event/${id}/citations`} className="text-[13px] px-4 py-2 border border-black rounded-none font-sans font-medium text-black bg-white hover:bg-neutral-100 transition-colors flex items-center gap-1.5 ml-auto">
          View citations (3) <ArrowRight className="w-3.5 h-3.5" />
        </Link>
        <Link href={`/event/${id}/notifications`} className="text-[13px] px-4 py-2 border border-black rounded-none font-sans font-medium text-black bg-white hover:bg-neutral-100 transition-colors flex items-center gap-1.5">
          Notification log <ArrowRight className="w-3.5 h-3.5" />
        </Link>
      </div>
    </div>
  );
}
