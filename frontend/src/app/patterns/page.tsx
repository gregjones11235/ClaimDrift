import { getPatterns } from "@/lib/api/client";
import { PatternList } from "./PatternList";

export default async function PatternsPage() {
  const { items: patterns } = await getPatterns();

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <h1 className="text-[22px] font-medium font-sans text-black mb-2 flex items-center gap-2">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-5.224 4.468C.69 10.63.13 11.83 1.13 13.06c.92 1.12 2.58 1.44 3.73 1.05.77 1.8 2.57 3.05 4.64 3.05 1.55 0 2.94-.74 3.8-1.89A5 5 0 0 0 17 17c2.76 0 5-2.24 5-5s-2.24-5-5-5a5 5 0 0 0-5-2z"></path></svg>
          Pattern memory
        </h1>
        <div className="text-[13px] text-[#666] font-sans">
          {patterns.length} pattern{patterns.length === 1 ? "" : "s"} — written by memory_synthesizer after processing drift events
        </div>
      </div>

      <PatternList patterns={patterns} />
    </div>
  );
}
