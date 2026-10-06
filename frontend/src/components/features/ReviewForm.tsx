"use client";

import { useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { bffErrorMessage, postReviewDecision } from "@/lib/api/client";
import {
  ABSTRACT_CLASS_LABEL,
  CITES_LABEL,
  CITES_ORDER,
  FULLTEXT_TIERS,
  ROLE_LABEL,
  ROLE_ORDER,
  ROOT_CAUSE_LABEL,
  shortDateTime,
} from "@/lib/labels";
import type {
  CitationCorrection,
  CitesClass,
  CitationRole,
  EventCorrection,
  ReviewDecision,
  ReviewDecisionResponse,
} from "@/types/claimdrift";
import { ReviewStatusBadge } from "./Badges";

const REVIEWER_KEY = "claimdrift.reviewer";
const ABSTRACT_CLASSES = ["no_change", "minor", "major"] as const;

// The reviewer name lives in localStorage so it survives between reviews.
// useSyncExternalStore reads it without a hydration mismatch (server snapshot "").
function subscribeStorage(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}
function readReviewer(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

const inputStyle: React.CSSProperties = {
  background: "var(--bk2)",
  border: "1px solid var(--gr3)",
  color: "var(--wh)",
  padding: "10px 12px",
  fontFamily: "var(--mono)",
  fontSize: 12,
  outline: "none",
  width: "100%",
};
const selectStyle: React.CSSProperties = { ...inputStyle, cursor: "pointer" };

/** One claim_diff as the event correction form needs it. */
export interface CorrectableDiff {
  idx: number;
  label: string;
  root_cause?: string | null;
  severity_tier?: string | null;
}

export function ReviewForm({
  kind,
  id,
  currentStatus,
  currentCites,
  currentRole,
  currentAbstractClass,
  currentFulltextTier,
  diffs,
}: {
  kind: "events" | "citations";
  id: string;
  currentStatus?: string | null;
  currentCites?: string | null;
  currentRole?: string | null;
  currentAbstractClass?: string | null;
  currentFulltextTier?: string | null;
  diffs?: CorrectableDiff[];
}) {
  const router = useRouter();
  const storedReviewer = useSyncExternalStore(subscribeStorage, readReviewer, () => "");
  const [typedReviewer, setTypedReviewer] = useState<string | null>(null);
  const reviewer = typedReviewer ?? storedReviewer;
  const [note, setNote] = useState("");
  // citation correction
  const [cites, setCites] = useState<string>("");
  const [role, setRole] = useState<string>("");
  // event correction
  const [abstractClass, setAbstractClass] = useState<string>("");
  const [fulltextTier, setFulltextTier] = useState<string>("");
  const [diffEdits, setDiffEdits] = useState<Record<number, { root_cause?: string; severity_tier?: string }>>({});
  const [busy, setBusy] = useState<ReviewDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReviewDecisionResponse | null>(null);

  function buildCorrection(): CitationCorrection | EventCorrection | undefined {
    if (kind === "citations") {
      const c: CitationCorrection = {};
      if (cites && cites !== currentCites) c.cites = cites as CitesClass;
      if (role && role !== currentRole) c.role = role as CitationRole;
      return Object.keys(c).length ? c : undefined;
    }
    const c: EventCorrection = {};
    if (abstractClass && abstractClass !== currentAbstractClass) c.abstract_class = abstractClass;
    if (fulltextTier && fulltextTier !== currentFulltextTier) c.fulltext_tier = fulltextTier;
    const rows = (diffs ?? [])
      .map((d) => {
        const e = diffEdits[d.idx] ?? {};
        const row: { idx: number; root_cause?: string; severity_tier?: string } = { idx: d.idx };
        if (e.root_cause && e.root_cause !== d.root_cause) row.root_cause = e.root_cause;
        if (e.severity_tier && e.severity_tier !== d.severity_tier) row.severity_tier = e.severity_tier;
        return row;
      })
      .filter((r) => r.root_cause || r.severity_tier);
    if (rows.length) c.claim_diffs = rows;
    return Object.keys(c).length ? c : undefined;
  }

  const correction = buildCorrection();

  async function submit(decision: ReviewDecision) {
    const name = reviewer.trim();
    setBusy(decision);
    setError(null);
    if (name) {
      try {
        window.localStorage.setItem(REVIEWER_KEY, name);
      } catch {
        /* storage disabled — the name is still sent */
      }
    }
    try {
      const res = await postReviewDecision(kind, id, {
        decision,
        reviewer: name || undefined,
        note: note.trim() || undefined,
        corrected: decision === "approved" ? correction : undefined,
      });
      setResult(res);
      router.refresh();
    } catch (e) {
      setError(bffErrorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const setDiff = (idx: number, key: "root_cause" | "severity_tier", value: string) =>
    setDiffEdits((prev) => ({ ...prev, [idx]: { ...prev[idx], [key]: value } }));

  return (
    <div className="cd-panel" style={{ marginBottom: 16 }}>
      <div className="cd-panel-header">
        <span className="cd-panel-label">Review decision</span>
        <span className="specimen" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          current: <ReviewStatusBadge status={result?.review_status ?? currentStatus ?? "not_required"} />
        </span>
      </div>
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", gap: 12 }}>
          <label>
            <div className="specimen" style={{ marginBottom: 4 }}>reviewer (optional)</div>
            <input value={reviewer} onChange={(e) => setTypedReviewer(e.target.value)} placeholder="your name" style={inputStyle} />
          </label>
          <label>
            <div className="specimen" style={{ marginBottom: 4 }}>note (optional)</div>
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="why approve / correct / reject" style={inputStyle} />
          </label>
        </div>

        <div className="specimen" style={{ lineHeight: 1.6 }}>
          correct the classification (optional) — anything left on &ldquo;keep&rdquo; stays as the machine judged it;
          the machine&rsquo;s original values are kept for comparison.
        </div>

        {kind === "citations" && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <select value={cites} onChange={(e) => setCites(e.target.value)} style={selectStyle} aria-label="correct cites">
              <option value="">cites: keep {currentCites ? `"${currentCites}"` : "as is"}</option>
              {CITES_ORDER.filter((c) => c !== currentCites).map((c) => (
                <option key={c} value={c}>{c} — {CITES_LABEL[c]}</option>
              ))}
            </select>
            <select value={role} onChange={(e) => setRole(e.target.value)} style={selectStyle} aria-label="correct role">
              <option value="">role: keep {currentRole ? `"${currentRole}"` : "as is"}</option>
              {ROLE_ORDER.filter((r) => r !== currentRole).map((r) => (
                <option key={r} value={r}>{r} — {ROLE_LABEL[r]}</option>
              ))}
            </select>
          </div>
        )}

        {kind === "events" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <select value={abstractClass} onChange={(e) => setAbstractClass(e.target.value)} style={selectStyle} aria-label="correct abstract class">
                <option value="">abstract-level: keep {currentAbstractClass ? `"${currentAbstractClass}"` : "as is"}</option>
                {ABSTRACT_CLASSES.filter((c) => c !== currentAbstractClass).map((c) => (
                  <option key={c} value={c}>{c} — {ABSTRACT_CLASS_LABEL[c] ?? c}</option>
                ))}
              </select>
              <select value={fulltextTier} onChange={(e) => setFulltextTier(e.target.value)} style={selectStyle} aria-label="correct full-text tier">
                <option value="">full-text tier: keep {currentFulltextTier ? `"${currentFulltextTier}"` : "as is"}</option>
                {FULLTEXT_TIERS.filter((t) => t !== currentFulltextTier).map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            {(diffs ?? []).map((d) => (
              <div key={d.idx} style={{ display: "grid", gridTemplateColumns: "1fr 220px 180px", gap: 12, alignItems: "center" }}>
                <div style={{ fontSize: 12, color: "var(--wh2)", lineHeight: 1.5 }}>
                  <span className="specimen">change {d.idx + 1}</span> {d.label}
                </div>
                <select
                  value={diffEdits[d.idx]?.root_cause ?? ""}
                  onChange={(e) => setDiff(d.idx, "root_cause", e.target.value)}
                  style={selectStyle}
                  aria-label={`correct root cause of change ${d.idx + 1}`}
                >
                  <option value="">root cause: keep {d.root_cause ?? "as is"}</option>
                  {Object.keys(ROOT_CAUSE_LABEL).filter((r) => r !== d.root_cause).map((r) => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
                <select
                  value={diffEdits[d.idx]?.severity_tier ?? ""}
                  onChange={(e) => setDiff(d.idx, "severity_tier", e.target.value)}
                  style={selectStyle}
                  aria-label={`correct tier of change ${d.idx + 1}`}
                >
                  <option value="">tier: keep {d.severity_tier ?? "as is"}</option>
                  {FULLTEXT_TIERS.filter((t) => t !== d.severity_tier).map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button className="cd-btn cd-btn-green" disabled={busy !== null} onClick={() => submit("approved")}>
            {busy === "approved" ? "Saving…" : correction ? "✓ Save correction & approve" : "✓ Approve as is"}
          </button>
          <button
            className="cd-btn"
            style={{ borderColor: "var(--rd)", color: "var(--rd)" }}
            disabled={busy !== null}
            onClick={() => submit("rejected")}
            title={kind === "events" ? "The event as a whole is wrong (e.g. a false drift)" : "This paper does not belong here at all"}
          >
            {busy === "rejected" ? "Saving…" : "✗ Reject"}
          </button>
          {error && <span className="specimen" style={{ color: "var(--rd)" }}>⚠ {error}</span>}
          {result && !error && (
            <span className="specimen specimen-g">
              saved: {result.review_status}
              {result.reviewer ? ` by ${result.reviewer}` : ""} · {shortDateTime(result.reviewed_at)}
              {result.corrected_fields?.length ? ` · corrected ${result.corrected_fields.join(", ")}` : ""}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
