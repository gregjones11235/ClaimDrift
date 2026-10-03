// Inline notice for a BFF call that failed (e.g. 503 local_es_required when the
// BFF runs without the local Elasticsearch). Same look as the playground banners.
export function BffNotice({ message, title = "Backend unavailable" }: { message: string; title?: string }) {
  return (
    <div
      style={{
        marginBottom: 16,
        padding: "10px 14px",
        border: "1px solid var(--y)",
        background: "rgba(255,200,0,0.06)",
        fontSize: 13,
        lineHeight: 1.6,
      }}
    >
      <span className="specimen specimen-y">⚠ {title}</span>
      <div style={{ marginTop: 6, color: "var(--gr)" }}>{message}</div>
    </div>
  );
}
