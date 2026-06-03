"use client";

import { DriftPattern } from "@/types/claimdrift";
import { Badge } from "@/components/ui/badge";
import Link from "next/link";
import dayjs from "dayjs";
import { useState } from "react";

export function PatternList({ patterns }: { patterns: DriftPattern[] }) {
  const [selectedTag, setSelectedTag] = useState<string>("All");

  // Get unique tags
  const allTags = Array.from(new Set(patterns.flatMap(p => p.domain_tags))).sort();
  const tags = ["All", ...allTags];

  const filteredPatterns = selectedTag === "All" 
    ? patterns 
    : patterns.filter(p => p.domain_tags.includes(selectedTag));

  if (patterns.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-[13px] font-sans text-[#666] italic bg-white border border-black rounded-none">
        Memory synthesizer hasn't run yet. Process drift events first.
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-6">
        {tags.map(tag => (
          <button
            key={tag}
            onClick={() => setSelectedTag(tag)}
            className={`px-3 py-1 rounded-none text-[12px] font-sans font-medium transition-colors border ${
              selectedTag === tag 
                ? "bg-black text-white border-black" 
                : "bg-white text-black border-black hover:bg-[#F5F5F5]"
            }`}
          >
            {tag}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {filteredPatterns.map(pattern => (
          <div key={pattern.pattern_id} className="border border-black p-5 bg-white flex flex-col hover:border-black transition-colors rounded-none">
            <div className="flex gap-1.5 mb-3 flex-wrap">
              {pattern.domain_tags.map(tag => (
                <div key={tag} className="text-[11px] font-sans font-medium text-[#1971C2] bg-[#E7F5FF] px-2 py-0.5 rounded-none border border-[#1971C2]">
                  {tag}
                </div>
              ))}
            </div>
            
            <div className="text-[11px] font-sans text-[#666] mb-2 flex items-center gap-1">
              pattern_type: <span className="font-mono text-black">{pattern.pattern_type}</span>
            </div>
            
            <div className="text-[13px] font-sans font-medium text-black mb-5 leading-snug flex-1">
              {pattern.pattern_description}
            </div>
            
            <div className="flex flex-col gap-1 mt-auto">
              <div className="flex justify-between text-[11px] font-sans">
                <span className="text-[#666]">support_count</span>
                <span className="font-medium text-black">{pattern.support_count}</span>
              </div>
              <div className="flex justify-between text-[11px] font-sans">
                <span className="text-[#666]">source_event_ids</span>
                <span className="font-mono text-black text-[10px]">
                  {pattern.source_event_ids.map((id, idx) => (
                    <span key={id}>
                      <Link href={`/event/${id}`} className="hover:underline">{id}</Link>
                      {idx < pattern.source_event_ids.length - 1 ? ", " : ""}
                    </span>
                  ))}
                </span>
              </div>
              <div className="flex justify-between text-[11px] font-sans">
                <span className="text-[#666]">created_at</span>
                <span className="text-[#666] text-[10px]">{dayjs(pattern.created_at).format("YYYY-MM-DD")}</span>
              </div>
              <div className="flex justify-between text-[11px] font-sans">
                <span className="text-[#666]">last_updated_at</span>
                <span className="text-[#666] text-[10px]">{dayjs(pattern.last_updated_at).format("YYYY-MM-DD")}</span>
              </div>
            </div>
          </div>
        ))}
        
        {/* Dashed placeholder card */}
        <div className="border border-dashed border-[#868E96] bg-[#F8F9FA] p-5 flex flex-col items-center justify-center min-h-[200px] text-center">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#666" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mb-2"><path d="M12 5v14M5 12h14"/></svg>
          <div className="text-[12px] font-sans text-[#666] leading-snug">
            More patterns as<br/>memory_synthesizer processes events
          </div>
        </div>
      </div>
    </div>
  );
}
