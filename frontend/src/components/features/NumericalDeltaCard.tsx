"use client";

import { useEffect, useState } from "react";
import { NumericalDelta } from "@/types/claimdrift";

function AnimatedNumber({ from, to, suffix }: { from: number; to: number; suffix: string }) {
  const [current, setCurrent] = useState(from);
  useEffect(() => {
    const duration = 800;
    const start = performance.now();
    let id: number;
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setCurrent(from + (to - from) * eased);
      if (t < 1) id = requestAnimationFrame(tick);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [from, to]);
  return <span>{current.toFixed(1)}{suffix}</span>;
}

export function NumericalDeltaCard({ delta }: { delta: NumericalDelta }) {
  if (!delta) return null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 2, background: "var(--gr3)", marginBottom: 4 }}>
      <div style={{ background: "var(--bk3)", padding: "14px 16px", display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" }}>
        <div className="specimen" style={{ marginBottom: 4 }}>metric</div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--y)", fontWeight: 700, marginTop: 5, wordBreak: "break-all" }}>
          {delta.metric}
        </div>
      </div>
      <div style={{ background: "var(--bk3)", padding: "14px 16px", display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" }}>
        <div className="specimen" style={{ marginBottom: 4 }}>preprint_value</div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 24, fontWeight: 700, color: "var(--gr2)", lineHeight: 1, marginTop: 5 }}>
          {delta.preprint_value}%
        </div>
      </div>
      <div style={{ background: "var(--bk2)", padding: "14px 16px", display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" }}>
        <div className="specimen specimen-y" style={{ marginBottom: 4 }}>published_value</div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 24, fontWeight: 700, color: "var(--wh)", lineHeight: 1, marginTop: 5 }}>
          <AnimatedNumber from={delta.preprint_value} to={delta.published_value} suffix="%" />
        </div>
      </div>
      <div style={{ background: "var(--bk3)", padding: "14px 16px", display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" }}>
        <div className="specimen specimen-r" style={{ marginBottom: 4 }}>relative_delta</div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 24, fontWeight: 700, color: "var(--rd)", lineHeight: 1, marginTop: 5 }}>
          <AnimatedNumber from={0} to={delta.relative_delta * 100} suffix="%" />
        </div>
      </div>
    </div>
  );
}
