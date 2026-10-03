import { AbstractSeverity, FulltextSeverity } from "@/types/claimdrift";
import {
  ABSTRACT_CLASS_COLOR,
  ABSTRACT_CLASS_LABEL,
  ABSTRACT_SCALE_LABEL,
  FULLTEXT_SCALE_LABEL,
  FULLTEXT_TIERS,
  FULLTEXT_TIER_COLOR,
  labelOf,
} from "@/lib/labels";

// The two severities measure different things (abstract wording on the
// Brierley 2022 scale vs. the full-text drift analysis) and are deliberately
// shown side by side, never combined into one number.
export function SeverityPanels({
  abstractSeverity,
  fulltextSeverity,
  legacyMateriality,
}: {
  abstractSeverity?: AbstractSeverity | null;
  fulltextSeverity?: FulltextSeverity | null;
  // top-level materiality_score, used when an old event has no fulltext_severity
  legacyMateriality?: number | null;
}) {
  return (
    <div className="cd-panel" style={{ marginBottom: 16 }}>
      <div className="cd-panel-header">
        <span className="cd-panel-label">Severity</span>
        <span className="specimen">two independent scales · shown separately, never combined</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
        <AbstractCell sev={abstractSeverity} />
        <div style={{ borderLeft: "1px solid var(--gr3)" }}>
          <FulltextCell sev={fulltextSeverity} legacyMateriality={legacyMateriality} />
        </div>
      </div>
    </div>
  );
}

function Steps({ steps, active, colors }: { steps: readonly string[]; active: string | null | undefined; colors: Record<string, string> }) {
  return (
    <div style={{ display: "flex", gap: 3, marginBottom: 6 }}>
      {steps.map((s) => (
        <div
          key={s}
          title={s}
          style={{
            flex: 1,
            height: 5,
            background: s === active ? colors[s] : "var(--gr3)",
          }}
        />
      ))}
    </div>
  );
}

function AbstractCell({ sev }: { sev?: AbstractSeverity | null }) {
  const cls = sev?.class ?? null;
  return (
    <div style={{ padding: "16px" }}>
      <div className="specimen" style={{ marginBottom: 10 }}>{ABSTRACT_SCALE_LABEL}</div>
      {cls ? (
        <>
          <div style={{ fontFamily: "var(--mono)", fontSize: 28, fontWeight: 700, color: ABSTRACT_CLASS_COLOR[cls] ?? "var(--wh)", lineHeight: 1.1, marginBottom: 10 }}>
            {labelOf(ABSTRACT_CLASS_LABEL, cls)}
          </div>
          <Steps steps={["no_change", "minor", "major"]} active={cls} colors={ABSTRACT_CLASS_COLOR} />
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
            <span className="specimen">no change</span>
            <span className="specimen">minor</span>
            <span className="specimen">major</span>
          </div>
          {sev?.changes && sev.changes.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
              {sev.changes.map((c, i) => (
                <div key={i} style={{ fontSize: 13, color: "var(--gr)", lineHeight: 1.5, padding: "6px 8px", background: "var(--bk3)", borderLeft: `2px solid ${ABSTRACT_CLASS_COLOR[c.degree ?? ""] ?? "var(--gr3)"}` }}>
                  {c.section && <span className="specimen" style={{ marginRight: 6 }}>{c.section}</span>}
                  {c.what}
                  {c.degree && <span className="specimen" style={{ marginLeft: 6 }}>· {c.degree}</span>}
                </div>
              ))}
            </div>
          )}
          <div className="specimen" style={{ color: "var(--gr2)" }}>
            {[sev?.scale, sev?.model, sev?.exemplar_set ? `exemplars: ${sev.exemplar_set}` : null].filter(Boolean).join(" · ")}
          </div>
        </>
      ) : (
        <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)", fontStyle: "italic" }}>
          Not assessed for this event.
        </div>
      )}
    </div>
  );
}

function FulltextCell({ sev, legacyMateriality }: { sev?: FulltextSeverity | null; legacyMateriality?: number | null }) {
  const tier = sev?.tier ?? null;
  const score = sev?.materiality_score ?? legacyMateriality ?? null;
  return (
    <div style={{ padding: "16px" }}>
      <div className="specimen" style={{ marginBottom: 10 }}>{FULLTEXT_SCALE_LABEL}</div>
      {tier ? (
        <>
          <div style={{ fontFamily: "var(--mono)", fontSize: 28, fontWeight: 700, color: FULLTEXT_TIER_COLOR[tier] ?? "var(--wh)", lineHeight: 1.1, marginBottom: 10, textTransform: "capitalize" }}>
            {tier}
          </div>
          <Steps steps={FULLTEXT_TIERS} active={tier} colors={FULLTEXT_TIER_COLOR} />
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
            {FULLTEXT_TIERS.map((t) => <span key={t} className="specimen">{t}</span>)}
          </div>
        </>
      ) : (
        <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)", fontStyle: "italic", marginBottom: 12 }}>
          {score != null ? "Tier not recorded (older event) — materiality score only." : "Not assessed for this event."}
        </div>
      )}
      {score != null && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
          <span className="specimen">materiality</span>
          <div style={{ flex: 1, height: 3, background: "var(--gr3)", position: "relative" }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.max(0, Math.min(1, score)) * 100}%`, background: "var(--y)" }} />
          </div>
          <span style={{ fontFamily: "var(--mono)", fontSize: 15, fontWeight: 700, color: "var(--wh)" }}>{score.toFixed(2)}</span>
        </div>
      )}
      {(sev?.model || sev?.prompt_version) && (
        <div className="specimen" style={{ color: "var(--gr2)" }}>
          {[sev?.model, sev?.prompt_version ? `prompt ${sev.prompt_version}` : null].filter(Boolean).join(" · ")}
        </div>
      )}
    </div>
  );
}

// Compact two-part chip for tables: "abstract X | full text Y", kept as two
// labelled values, not a combined score.
export function SeverityPair({
  abstractClass,
  fulltextTier,
  legacyMateriality,
}: {
  abstractClass?: string | null;
  fulltextTier?: string | null;
  legacyMateriality?: number | null;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, fontFamily: "var(--mono)", fontSize: 12, whiteSpace: "nowrap" }}>
      <span title={FULLTEXT_SCALE_LABEL}>
        <span style={{ color: "var(--gr2)" }}>full text </span>
        {fulltextTier ? (
          <span style={{ color: FULLTEXT_TIER_COLOR[fulltextTier] ?? "var(--wh)", fontWeight: 700 }}>{fulltextTier}</span>
        ) : legacyMateriality != null ? (
          <span style={{ color: "var(--wh2)", fontWeight: 700 }}>{legacyMateriality.toFixed(2)}</span>
        ) : (
          <span style={{ color: "var(--gr2)" }}>—</span>
        )}
      </span>
      <span title={ABSTRACT_SCALE_LABEL}>
        <span style={{ color: "var(--gr2)" }}>abstract </span>
        {abstractClass ? (
          <span style={{ color: ABSTRACT_CLASS_COLOR[abstractClass] ?? "var(--wh)", fontWeight: 700 }}>
            {labelOf(ABSTRACT_CLASS_LABEL, abstractClass).toLowerCase()}
          </span>
        ) : (
          <span style={{ color: "var(--gr2)" }}>—</span>
        )}
      </span>
    </div>
  );
}
