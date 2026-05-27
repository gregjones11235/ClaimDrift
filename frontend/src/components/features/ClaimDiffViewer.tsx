import { ClaimDiff } from "@/types/claimdrift";
import { FileDiff } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export function ClaimDiffViewer({ diff }: { diff: ClaimDiff }) {
  const getBadgeLabel = (type: string) => {
    switch (type) {
      case "claim_disappeared": return "Retracted";
      case "claim_added": return "Added";
      case "numerical_shift": return "Value changed";
      case "hedging_added": return "Hedging added";
      case "hedging_removed": return "Hedging removed";
      case "claim_reversed": return "Reversed";
      default: return type;
    }
  };

  const isDisappeared = diff.diff_type === "claim_disappeared";
  const isReversed = diff.diff_type === "claim_reversed";

  return (
    <div className="grid grid-cols-2 gap-4 mt-4">
      {/* LEFT: Preprint */}
      <div className={`border border-black flex flex-col ${isDisappeared || isReversed ? 'bg-[#EAEAEA]' : 'bg-[#F5F5F5]'}`}>
        <div className="border-b border-black p-3 bg-[#EAEAEA] flex items-center gap-2 text-[12px] font-sans text-black">
          <FileDiff className="w-3.5 h-3.5" /> Preprint version &middot; hedging_level: none
        </div>
        <div className="p-4 flex-1">
          <div className="flex flex-wrap gap-2 mb-3">
            <Badge variant="outline" className="text-[10px] px-2 py-0.5 font-sans border-black text-[#666] bg-white rounded-none">
              {diff.diff_type}
            </Badge>
            <Badge variant="outline" className="text-[10px] px-2 py-0.5 font-sans border-black text-black bg-white rounded-none">
              claim_type: quantitative
            </Badge>
            {isDisappeared && <Badge variant="destructive" className="bg-black text-white border-black rounded-none text-[10px] px-1.5 py-0 hover:bg-black">Retracted</Badge>}
            {isReversed && <Badge variant="destructive" className="bg-black text-white border-black rounded-none text-[10px] px-1.5 py-0 hover:bg-black">Reversed</Badge>}
          </div>
          <div className="text-[13px] leading-[1.6] text-black font-sans">
            {diff.preprint_text}
          </div>
        </div>
      </div>

      {/* RIGHT: Published */}
      <div className={`border border-black flex flex-col ${isReversed ? 'bg-[#EAEAEA]' : 'bg-white'}`}>
        <div className="border-b border-black p-3 bg-[#F5F5F5] flex items-center gap-2 text-[12px] font-sans text-black">
          <FileDiff className="w-3.5 h-3.5" /> Published version &middot; hedging_level: strong
        </div>
        <div className="p-4 flex-1">
          {!isDisappeared ? (
            <>
              <div className="flex flex-wrap gap-2 mb-3">
                <Badge variant="outline" className="text-[10px] px-2 py-0.5 font-sans border-black text-black bg-white rounded-none">
                  {diff.diff_type}
                </Badge>
                <Badge variant="outline" className="text-[10px] px-2 py-0.5 font-sans border-black text-black bg-white rounded-none">
                  hedging added
                </Badge>
              </div>
              <div className="text-[13px] leading-[1.6] text-black font-sans">
                {diff.published_text}
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center h-full text-[13px] text-[#666] italic">
              Claim was removed in published version.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
