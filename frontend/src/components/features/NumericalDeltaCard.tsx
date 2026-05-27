"use client";

import { useEffect, useState } from "react";
import { NumericalDelta } from "@/types/claimdrift";

function AnimatedNumber({ from, to, suffix }: { from: number; to: number; suffix: string }) {
  const [current, setCurrent] = useState(from);

  useEffect(() => {
    const duration = 800;
    const start = performance.now();
    let animationFrameId: number;

    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out-cubic
      setCurrent(from + (to - from) * eased);
      if (t < 1) {
        animationFrameId = requestAnimationFrame(tick);
      }
    };
    
    animationFrameId = requestAnimationFrame(tick);
    
    return () => cancelAnimationFrame(animationFrameId);
  }, [from, to]);

  return <span>{current.toFixed(1)}{suffix}</span>;
}

export function NumericalDeltaCard({ delta }: { delta: NumericalDelta }) {
  if (!delta) return null;

  return (
    <div className="grid grid-cols-4 gap-4 mb-6">
      <div className="border border-black bg-[#F5F5F5] p-4 flex flex-col justify-center items-center text-center">
        <div className="text-[11px] font-mono text-[#666] mb-1">metric</div>
        <div className="text-[13px] font-mono font-medium text-black leading-snug">
          {delta.metric}
        </div>
      </div>
      
      <div className="border border-black bg-[#F5F5F5] p-4 flex flex-col justify-center items-center">
        <div className="text-[11px] font-mono text-[#666] mb-1">preprint_value</div>
        <div className="text-2xl font-mono text-[#666]">
          {delta.preprint_value}%
        </div>
      </div>
      
      <div className="border border-black bg-white p-4 flex flex-col justify-center items-center">
        <div className="text-[11px] font-mono text-[#666] mb-1">published_value</div>
        <div className="text-2xl font-mono text-black font-medium">
          <AnimatedNumber from={delta.preprint_value} to={delta.published_value} suffix="%" />
        </div>
      </div>
      
      <div className="border border-black bg-white p-4 flex flex-col justify-center items-center">
        <div className="text-[11px] font-mono text-[#666] mb-1">relative_delta</div>
        <div className="text-2xl font-mono text-black font-medium">
          <AnimatedNumber from={0} to={delta.relative_delta * 100} suffix="%" />
        </div>
      </div>
    </div>
  );
}
