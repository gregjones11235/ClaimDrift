import { getAffectedCitations } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { ArrowLeft, Bell, AlertCircle, AlertTriangle, Quote } from "lucide-react";
import Link from "next/link";
import dayjs from "dayjs";
import { AffectedCitation } from "@/types/claimdrift";

function CitationCard({ citation, id }: { citation: AffectedCitation, id: string }) {
  const authorNames = citation.citing_paper_authors.map(a => a.name).join(", ");
  
  return (
    <div className="p-4 bg-white">
      <div className="font-sans font-medium text-[14px] text-black mb-1 leading-snug">
        {citation.citing_paper_title}
      </div>
      <div className="font-mono text-[11px] text-[#666] mb-3">
        citing_paper_doi: {citation.citing_paper_doi}
      </div>
      
      <div className="font-sans text-[12px] text-[#666] mb-4 italic flex gap-2">
        <Quote className="w-3.5 h-3.5 shrink-0 mt-0.5 text-[#666]" />
        <div>
          <span className="font-medium text-[#666] whitespace-nowrap">severity_reasoning:</span>
          <span> "{citation.severity_reasoning}"</span>
        </div>
      </div>
      
      <div className="flex justify-between items-center pt-3 border-t border-black/10">
        <div className="font-sans text-[11px] text-[#666]">
          {authorNames} &middot; {dayjs(citation.scored_at).format('MMM D, YYYY')}
        </div>
        <Link 
          href={`/event/${id}/notifications`}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-black rounded-none font-sans text-[11px] font-medium text-black bg-white hover:bg-neutral-100 transition-colors"
        >
          <Bell className="w-3.5 h-3.5" /> Notify this author
        </Link>
      </div>
    </div>
  );
}

export default async function CitationsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { items: citations } = await getAffectedCitations(id);

  const central = citations.filter(c => c.severity_tier === "central");
  const comparative = citations.filter(c => c.severity_tier === "comparative");
  const peripheral = citations.filter(c => c.severity_tier === "peripheral");

  return (
    <div className="max-w-4xl">
      <Link href={`/event/${id}`} className="inline-flex items-center gap-1.5 text-[13px] font-sans text-black hover:underline mb-6 transition-colors">
        <ArrowLeft className="w-3.5 h-3.5" /> Back to Drift Detail
      </Link>

      <div className="mb-6">
        <h1 className="text-[22px] font-medium font-sans text-black mb-2">
          Affected Citations
        </h1>
        <div className="text-[13px] text-[#666] font-sans">
          These papers cite the preprint version of the modified claim.
        </div>
      </div>

      <Accordion type="multiple" defaultValue={["item-central", "item-comparative", "item-peripheral"]} className="space-y-4">
        <AccordionItem value="item-central" className="border border-black bg-white">
          <AccordionTrigger className="hover:no-underline p-3 bg-[#FFF5F5] data-[state=open]:border-b data-[state=open]:border-black transition-colors">
            <div className="flex justify-between items-center w-full pr-4">
              <div className="flex items-center gap-2 text-[#C92A2A] font-medium text-[13px]">
                <AlertCircle className="w-4 h-4" />
                severity_tier: central
              </div>
              <Badge variant="outline" className="text-[#C92A2A] border-[#C92A2A] bg-white rounded-none text-[10px] px-2 py-0.5">
                {central.length} {central.length === 1 ? 'paper' : 'papers'}
              </Badge>
            </div>
          </AccordionTrigger>
          <AccordionContent className="p-0">
            {central.length === 0 ? (
              <div className="p-4 text-[13px] text-[#666] italic font-sans bg-white">No central citations found.</div>
            ) : (
              <div className="divide-y divide-black/10">
                {central.map(c => <CitationCard key={c.affected_citation_id} citation={c} id={id} />)}
              </div>
            )}
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="item-comparative" className="border border-black bg-white">
          <AccordionTrigger className="hover:no-underline p-3 bg-[#FFF9DB] data-[state=open]:border-b data-[state=open]:border-black transition-colors">
            <div className="flex justify-between items-center w-full pr-4">
              <div className="flex items-center gap-2 text-[#B45309] font-medium text-[13px]">
                <AlertTriangle className="w-4 h-4" />
                severity_tier: comparative
              </div>
              <Badge variant="outline" className="text-[#B45309] border-[#B45309] bg-white rounded-none text-[10px] px-2 py-0.5">
                {comparative.length} {comparative.length === 1 ? 'paper' : 'papers'}
              </Badge>
            </div>
          </AccordionTrigger>
          <AccordionContent className="p-0">
            {comparative.length === 0 ? (
              <div className="p-4 text-[13px] text-[#666] italic font-sans bg-white">No comparative citations found.</div>
            ) : (
              <div className="divide-y divide-black/10">
                {comparative.map(c => <CitationCard key={c.affected_citation_id} citation={c} id={id} />)}
              </div>
            )}
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="item-peripheral" className="border border-black bg-white">
          <AccordionTrigger className="hover:no-underline p-3 bg-[#F5F5F5] data-[state=open]:border-b data-[state=open]:border-black transition-colors">
            <div className="flex justify-between items-center w-full pr-4">
              <div className="flex items-center gap-2 text-[#666] font-medium text-[13px]">
                <div className="w-2 h-2 rounded-full bg-[#666]" />
                severity_tier: peripheral
              </div>
              <Badge variant="outline" className="text-[#666] border-[#666] bg-white rounded-none text-[10px] px-2 py-0.5">
                {peripheral.length} {peripheral.length === 1 ? 'paper' : 'papers'}
              </Badge>
            </div>
          </AccordionTrigger>
          <AccordionContent className="p-0">
            {peripheral.length === 0 ? (
              <div className="p-4 text-[13px] text-[#666] italic font-sans bg-[#F9F9F9]">
                No peripheral citations for this event.
              </div>
            ) : (
              <div className="divide-y divide-black/10">
                {peripheral.map(c => <CitationCard key={c.affected_citation_id} citation={c} id={id} />)}
              </div>
            )}
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
}
