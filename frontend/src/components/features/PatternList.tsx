"use client";

import { useState } from "react";
import { DriftPattern } from "@/types/claimdrift";
import Link from "next/link";
import dayjs from "dayjs";

const TYPE_COLORS: Record<string, string> = {
  effect_size_reduction: "var(--rd)",
  hedging_addition:      "var(--y)",
  outcome_switch:        "var(--or)",
  numerical_softening:   "var(--bl)",
  claim_disappearance:   "var(--gr2)",
  other:                 "var(--gr)",
};

export function PatternList({ patterns }: { patterns: DriftPattern[] }) {
  const allTags = Array.from(new Set(patterns.flatMap((p) => p.domain_tags))).sort();
  const [activeTag, setActiveTag] = useState("All");
  const [tagsOpen, setTagsOpen] = useState(false);

  const maxSupport = Math.max(...patterns.map((p) => p.support_count), 1);
  const filtered = activeTag === "All" ? patterns : patterns.filter((p) => p.domain_tags.includes(activeTag));

  const tagOptions = ["All", ...allTags];
  const selectTag = (tag: string) => {
    setActiveTag(tag);
    setTagsOpen(false);
  };

  if (patterns.length === 0) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 200, border: "1px dashed var(--gr3)", background: "var(--bk3)", fontFamily: "var(--mono)", fontSize: 14, color: "var(--gr)", fontStyle: "italic" }}>
        Memory synthesizer hasn&apos;t run yet. Process drift events first.
      </div>
    );
  }

  return (
    <>
      {/* Tag filter — collapsible dropdown (domain tags can be dozens-wide) */}
      <div style={{ position: "relative", marginBottom: 2, background: "var(--bk2)", borderBottom: "1px solid var(--gr3)" }}>
        <button
          onClick={() => setTagsOpen((v) => !v)}
          style={{
            display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10,
            width: "100%", padding: "14px 20px", border: "none", cursor: "pointer",
            fontFamily: "var(--mono)", fontSize: 14, letterSpacing: "0.08em", textTransform: "uppercase",
            color: activeTag === "All" ? "var(--gr)" : "var(--y)",
            background: activeTag === "All" ? "var(--bk2)" : "rgba(245,197,24,0.08)",
          }}>
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ color: "var(--gr2)", textTransform: "uppercase", letterSpacing: "0.1em" }}>filter:</span>
            <span>{activeTag}</span>
            {activeTag !== "All" && <span className="specimen" style={{ color: "var(--gr2)" }}>({filtered.length})</span>}
          </span>
          {/* chevron: down when open, right when closed */}
          <svg width="11" height="11" viewBox="0 0 10 10" fill="none" stroke="currentColor"
            strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
            style={{ transform: tagsOpen ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s", opacity: 0.7 }}>
            <path d="M3 1.5 6.5 5 3 8.5" />
          </svg>
        </button>

        {tagsOpen && (
          <div style={{
            position: "absolute", top: "100%", left: 0, right: 0, zIndex: 30,
            background: "var(--bk2)", border: "1px solid var(--gr3)", borderTop: "none",
            maxHeight: 320, overflowY: "auto",
            display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 1,
            padding: 1,
          }}>
            {tagOptions.map((tag) => (
              <button key={tag} onClick={() => selectTag(tag)}
                style={{
                  padding: "9px 14px", border: "none", cursor: "pointer", textAlign: "left",
                  fontFamily: "var(--mono)", fontSize: 12, letterSpacing: "0.05em", textTransform: "uppercase",
                  color: activeTag === tag ? "var(--y)" : "var(--gr)",
                  background: activeTag === tag ? "rgba(245,197,24,0.08)" : "var(--bk3)",
                  transition: "color 0.12s",
                }}
                onMouseEnter={(e) => { if (activeTag !== tag) (e.currentTarget as HTMLElement).style.color = "var(--wh)"; }}
                onMouseLeave={(e) => { if (activeTag !== tag) (e.currentTarget as HTMLElement).style.color = "var(--gr)"; }}>
                {tag}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2, background: "var(--gr3)", padding: 2 }}>
        {filtered.map((pattern) => {
          const color = TYPE_COLORS[pattern.pattern_type] ?? "var(--gr)";
          const pct = Math.round((pattern.support_count / maxSupport) * 100);
          return (
            <div key={pattern.pattern_id}
              style={{ background: "var(--bk2)", padding: 16, display: "flex", flexDirection: "column", cursor: "default", transition: "background 0.15s", position: "relative", borderLeft: `3px solid ${color}` }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bk3)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bk2)")}>

              {/* Top row: type badge + support bar */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 9 }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", padding: "3px 7px", border: `1px solid ${color}`, color }}>
                  {pattern.pattern_type}
                </span>
                <div style={{ display: "flex", alignItems: "center", gap: 7, width: 110 }}>
                  <span className="specimen">n={pattern.support_count}</span>
                  <div style={{ height: 3, background: "var(--gr3)", flex: 1, position: "relative" }}>
                    <div style={{ height: "100%", background: color, width: `${pct}%`, transition: "width 0.6s ease" }} />
                  </div>
                </div>
              </div>

              {/* Description */}
              <div style={{ fontSize: 14, fontWeight: 300, color: "var(--wh2)", lineHeight: 1.7, marginBottom: 10, flex: 1 }}>
                {pattern.pattern_description}
              </div>

              {/* Domain tags */}
              <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginBottom: 10 }}>
                {pattern.domain_tags.map((tag) => (
                  <span key={tag} style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.06em", padding: "2px 6px", border: "1px solid var(--gr3)", color: "var(--bl)", background: "rgba(74,158,255,0.05)" }}>
                    {tag}
                  </span>
                ))}
              </div>

              {/* Footer metadata */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, borderTop: "1px solid var(--gr3)", paddingTop: 9, marginTop: "auto" }}>
                <div>
                  <div className="specimen" style={{ marginBottom: 1 }}>support_count</div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 18, color, fontWeight: 700 }}>{pattern.support_count}</div>
                </div>
                <div>
                  <div className="specimen" style={{ marginBottom: 1 }}>last_updated</div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr2)" }}>{dayjs(pattern.last_updated_at).format("YYYY-MM-DD")}</div>
                </div>
                <div style={{ gridColumn: "1 / -1" }}>
                  <div className="specimen" style={{ marginBottom: 2 }}>source_events</div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--bl)", lineHeight: 1.5 }}>
                    {pattern.source_event_ids.slice(0, 2).map((id, idx) => (
                      <span key={id}>
                        <Link href={`/event/${id}`} style={{ color: "var(--bl)", textDecoration: "none" }} onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.textDecoration = "underline")} onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.textDecoration = "none")}>{id.slice(0, 8)}…</Link>
                        {idx < Math.min(pattern.source_event_ids.length, 2) - 1 ? ", " : ""}
                      </span>
                    ))}
                    {pattern.source_event_ids.length > 2 && <span style={{ color: "var(--gr2)" }}> +{pattern.source_event_ids.length - 2} more</span>}
                  </div>
                </div>
              </div>
            </div>
          );
        })}

        {/* Placeholder */}
        <div style={{ background: "var(--bk3)", border: "1px dashed var(--gr3)", padding: 24, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 7, textAlign: "center" }}>
          <div style={{ width: 24, height: 24, border: "1px dashed var(--gr3)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--gr2)", fontSize: 18 }}>+</div>
          <span className="specimen specimen-p">More patterns as memory_synthesizer processes events</span>
        </div>
      </div>
    </>
  );
}
