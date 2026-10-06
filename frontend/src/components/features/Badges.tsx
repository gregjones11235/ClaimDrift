import {
  CITES_COLOR,
  CITES_LABEL,
  FLAG_EXPLANATION,
  REVIEW_REASON_LABEL,
  REVIEW_STATUS_COLOR,
  REVIEW_STATUS_LABEL,
  VERIFICATION_COLOR,
  VERIFICATION_EXPLANATION,
  VERIFICATION_LABEL,
  labelOf,
} from "@/lib/labels";

// A .cd-badge tinted with an arbitrary theme colour (the fixed .cd-badge-*
// variants only cover five colours).
export function ColorBadge({
  color,
  children,
  title,
}: {
  color: string;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <span
      className="cd-badge"
      title={title}
      style={{
        borderColor: color,
        color,
        background: `color-mix(in srgb, ${color} 7%, transparent)`,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

export function ReviewStatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  return (
    <ColorBadge color={REVIEW_STATUS_COLOR[status] ?? "var(--gr)"}>
      {labelOf(REVIEW_STATUS_LABEL, status)}
    </ColorBadge>
  );
}

// Customer views: how far a finding has been checked (auto-verified / human-confirmed / provisional). The operator
// review queue keeps ReviewStatusBadge.
export function VerificationBadge({ status }: { status: string | null | undefined }) {
  if (!status || !VERIFICATION_LABEL[status]) return null;
  return (
    <ColorBadge color={VERIFICATION_COLOR[status]} title={VERIFICATION_EXPLANATION[status]}>
      {VERIFICATION_LABEL[status]}
    </ColorBadge>
  );
}

export function ReasonBadges({ reasons }: { reasons: string[] | null | undefined }) {
  if (!reasons || reasons.length === 0) return null;
  return (
    <>
      {reasons.map((r) => (
        <span key={r} className="cd-badge cd-badge-o" title={r} style={{ textTransform: "none", letterSpacing: "0.02em" }}>
          {labelOf(REVIEW_REASON_LABEL, r)}
        </span>
      ))}
    </>
  );
}

export function CitesBadge({ cites }: { cites: string | null | undefined }) {
  if (!cites) return null;
  return (
    <ColorBadge color={CITES_COLOR[cites] ?? "var(--gr)"} title={cites}>
      {labelOf(CITES_LABEL, cites)}
    </ColorBadge>
  );
}

export function FlagBadges({ flags, explain = false }: { flags: string[] | null | undefined; explain?: boolean }) {
  if (!flags || flags.length === 0) return null;
  return (
    <>
      {flags.map((f) => (
        <span key={f} className="cd-badge cd-badge-y" title={FLAG_EXPLANATION[f] ?? f} style={{ textTransform: "none" }}>
          {f}
          {explain && FLAG_EXPLANATION[f] ? ` · ${FLAG_EXPLANATION[f]}` : ""}
        </span>
      ))}
    </>
  );
}

// ✓ / ✗ / ? mark for a verbatim-quote check.
export function VerifiedMark({ verified, label }: { verified: boolean | null | undefined; label?: string }) {
  const color = verified === true ? "var(--grn)" : verified === false ? "var(--rd)" : "var(--gr2)";
  const sym = verified === true ? "✓" : verified === false ? "✗" : "?";
  const text =
    label ?? (verified === true ? "verified verbatim" : verified === false ? "not found verbatim" : "not checked");
  return (
    <span style={{ fontFamily: "var(--mono)", fontSize: 11, color, whiteSpace: "nowrap" }} title={text}>
      {sym} {text}
    </span>
  );
}
