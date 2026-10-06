"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  bffErrorMessage,
  bffErrorTitle,
  getSelfcheckAnalyze,
  postSelfcheckAnalyze,
  postSelfcheckPublished,
  postSelfcheckReferences,
  postSelfcheckSentences,
} from "@/lib/api/client";
import type {
  OnDemandStatus,
  SearchMode,
  SelfcheckAnalyzeResponse,
  SelfcheckPrecheck,
  SelfcheckReferenceResult,
  SelfcheckPublishedResponse,
  SelfcheckReferencesResponse,
  SelfcheckSentenceMatch,
  SelfcheckSentencesResponse,
  ValueVerdict,
} from "@/types/claimdrift";
import { BffNotice } from "@/components/features/BffNotice";
import { CitesBadge, ColorBadge, VerificationBadge, VerifiedMark } from "@/components/features/Badges";
import { SeverityPair } from "@/components/features/SeverityPanels";
import { FULLTEXT_TIER_COLOR, ROLE_LABEL, ROOT_CAUSE_LABEL, labelOf } from "@/lib/labels";

type NoticeError = { title: string; message: string };

// A failed self-check call: "Check your input" for a 400 (e.g. not a DOI / PMCID), "Backend unavailable" otherwise.
function noticeError(e: unknown): NoticeError {
  return { message: bffErrorMessage(e), title: bffErrorTitle(e) };
}

const MAX_SENTENCES = 20;
const POLL_MS = 3_000;

const textareaStyle: React.CSSProperties = {
  width: "100%",
  minHeight: 180,
  background: "var(--bk2)",
  border: "1px solid var(--gr3)",
  color: "var(--wh)",
  padding: "12px 14px",
  fontFamily: "var(--mono)",
  fontSize: 12,
  lineHeight: 1.7,
  outline: "none",
  resize: "vertical",
};

const runBtnStyle = (enabled: boolean): React.CSSProperties => ({
  background: enabled ? "var(--y)" : "var(--gr3)",
  color: enabled ? "var(--bk)" : "var(--gr)",
  border: `1px solid ${enabled ? "var(--y)" : "var(--gr3)"}`,
  padding: "12px 26px",
  fontFamily: "var(--mono)",
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: "0.14em",
  textTransform: "uppercase",
  cursor: enabled ? "pointer" : "not-allowed",
});

export default function SelfcheckPage() {
  const [tab, setTab] = useState<"before" | "after">("before");

  return (
    <div style={{ maxWidth: 1180 }}>
      <p style={{ fontSize: 13, lineHeight: 1.7, color: "var(--gr)", maxWidth: 820, marginBottom: 16 }}>
        Find out whether a preprint you cite had its claim revised in the published version. Before submission, paste
        your reference list and the sentences that cite preprints. After publication, enter your paper&rsquo;s DOI or
        PMCID and ClaimDrift reads it for you. Only drift events already in the library can be matched; a
        bioRxiv/medRxiv preprint that is not in the library can be analysed on demand.
      </p>

      <div className="cd-panel" style={{ marginBottom: 16 }}>
        <div className="cd-filter-row">
          <button className="cd-filter-tab" data-active={tab === "before" ? "true" : "false"} onClick={() => setTab("before")}>
            Before submission · check my manuscript
          </button>
          <button className="cd-filter-tab" data-active={tab === "after" ? "true" : "false"} onClick={() => setTab("after")}>
            After publication · check my published paper
          </button>
        </div>
      </div>

      {/* Both stay mounted so switching tabs keeps input and results. */}
      <div style={{ display: tab === "before" ? "block" : "none" }}>
        <ManuscriptCheck />
      </div>
      <div style={{ display: tab === "after" ? "block" : "none" }}>
        <PublishedCheck />
      </div>
    </div>
  );
}

// ── Before submission: reference list + citing sentences, one run ────────────
// The two inputs go to different checks (references: DOI / title lookup, no model call; sentences: hybrid retrieval +
// one Gemini call each), so they stay two fields rather than one box split by guesswork. Either may be left empty.
function ManuscriptCheck() {
  const [refText, setRefText] = useState("");
  const [sentText, setSentText] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [busy, setBusy] = useState(false);
  const [refError, setRefError] = useState<NoticeError | null>(null);
  const [sentError, setSentError] = useState<NoticeError | null>(null);
  const [refRes, setRefRes] = useState<SelfcheckReferencesResponse | null>(null);
  const [sentRes, setSentRes] = useState<SelfcheckSentencesResponse | null>(null);

  const sentences = sentText.split("\n").map((x) => x.trim()).filter(Boolean);
  const tooMany = sentences.length > MAX_SENTENCES;
  const hasRefs = refText.trim().length > 0;
  const hasSents = sentences.length > 0;

  async function run() {
    setBusy(true);
    setRefError(null);
    setSentError(null);
    setRefRes(null);
    setSentRes(null);
    await Promise.all([
      hasRefs
        ? postSelfcheckReferences(refText).then(setRefRes, (e) => setRefError(noticeError(e)))
        : Promise.resolve(),
      hasSents
        ? postSelfcheckSentences(sentences.slice(0, MAX_SENTENCES), mode).then(setSentRes, (e) => setSentError(noticeError(e)))
        : Promise.resolve(),
    ]);
    setBusy(false);
  }

  const enabled = !busy && (hasRefs || hasSents) && !tooMany;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <div>
          <div className="specimen" style={{ marginBottom: 6, whiteSpace: "nowrap" }}>reference list</div>
          <textarea
            value={refText}
            onChange={(e) => setRefText(e.target.value)}
            placeholder={"One reference per line, e.g.\nSmith J, et al. Title of the preprint. medRxiv 2024. doi:10.1101/2024.05.01.24306384"}
            style={textareaStyle}
          />
        </div>
        <div>
          <div className="specimen" style={{ marginBottom: 6, whiteSpace: "nowrap", color: tooMany ? "var(--rd)" : undefined }}>
            citing sentences · {sentences.length}/{MAX_SENTENCES}{tooMany ? " · too many" : ""}
          </div>
          <textarea
            value={sentText}
            onChange={(e) => setSentText(e.target.value)}
            placeholder={"One sentence that cites a preprint per line (max 20), e.g.\nThe basic reproduction number was estimated at 5.8 (Sanche et al.)."}
            style={textareaStyle}
          />
        </div>
      </div>
      <div style={{ display: "flex", gap: 12, alignItems: "center", margin: "10px 0 18px", flexWrap: "wrap" }}>
        <button onClick={run} disabled={!enabled} style={runBtnStyle(enabled)}>
          {busy ? "Checking…" : "▶ Check manuscript"}
        </button>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="specimen">sentence retrieval</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as SearchMode)}
            style={{ background: "var(--bk2)", border: "1px solid var(--gr3)", color: "var(--wh)", padding: "9px 10px", fontFamily: "var(--mono)", fontSize: 12, outline: "none" }}
          >
            <option value="hybrid">hybrid (BM25 + ELSER, default)</option>
            <option value="elser">ELSER (semantic)</option>
            <option value="bm25">BM25 (keywords)</option>
          </select>
        </label>
        <span className="specimen">fill in either field or both</span>
      </div>

      {refError && <BffNotice title={refError.title} message={refError.message} />}
      {refRes && <ReferenceResults res={refRes} />}

      {sentError && <BffNotice title={sentError.title} message={sentError.message} />}
      {sentRes && <SentenceResults res={sentRes} />}
    </div>
  );
}

// ── Reference list results ──────────────────────────────────────────────────
function ReferenceResults({ res }: { res: SelfcheckReferencesResponse }) {
  return (
    <div className="cd-panel" style={{ marginBottom: 18 }}>
      <div className="cd-panel-header">
        <span className="cd-panel-label">Reference list</span>
        <span className="specimen">{res.n_lines} line(s) checked</span>
      </div>
      <ReferenceSummary res={res} />
      {res.results.length === 0 && (
        <div style={{ padding: 16, fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)", fontStyle: "italic" }}>
          No references found in the text.
        </div>
      )}
      {res.results.map((r, i) => (
        <ReferenceRow key={i} r={r} initialStatus={r.doi ? res.not_in_library_status?.[r.doi] ?? null : null} />
      ))}
    </div>
  );
}

// One-line overview above the per-reference rows: what needs attention first.
function ReferenceSummary({ res }: { res: SelfcheckReferencesResponse }) {
  const s = res.summary;
  if (!s) return null;
  const parts: { n: number; text: string; color: string }[] = [
    { n: s.drift_found, text: "cited preprint(s) were revised after v1", color: "var(--rd)" },
    { n: s.significant_or_major, text: "of them significantly (significant / major)", color: "var(--rd)" },
    { n: s.needs_confirmation, text: "matched by title only — please confirm", color: "var(--y)" },
    { n: s.not_in_library, text: "preprint(s) not in the library yet", color: "var(--y)" },
    { n: s.not_tracked, text: "not tracked (not a preprint)", color: "var(--gr)" },
    { n: s.no_match, text: "without DOI and no title match", color: "var(--gr)" },
    { n: s.skipped, text: "skipped (could not be read)", color: "var(--gr)" },
  ];
  return (
    <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--gr3)", display: "flex", gap: 18, flexWrap: "wrap" }}>
      {parts.filter((p) => p.n > 0).map((p) => (
        <span key={p.text} style={{ fontSize: 13, color: "var(--wh2)" }}>
          <strong style={{ color: p.color, fontFamily: "var(--mono)" }}>{p.n}</strong> {p.text}
        </span>
      ))}
      {s.drift_found === 0 && (
        <span style={{ fontSize: 13, color: "var(--grn)" }}>No cited preprint in the library was revised.</span>
      )}
      {res.truncated && (
        <span className="specimen" style={{ color: "var(--rd)" }}>input over 100,000 characters — only the first part was checked</span>
      )}
    </div>
  );
}

const REF_STATUS: Record<string, { label: string; color: string }> = {
  drift_found: { label: "Drift found", color: "var(--rd)" },
  not_in_library: { label: "Not in library", color: "var(--y)" },
  not_tracked: { label: "Not tracked", color: "var(--gr)" },
  no_match: { label: "No match", color: "var(--gr)" },
  skipped: { label: "Skipped", color: "var(--gr2)" },
};

function refStatus(r: SelfcheckReferenceResult): { label: string; color: string } {
  if (r.status === "drift_found") {
    const t = r.event?.fulltext_tier;
    if (t === "minor") return { label: "Minor changes", color: "var(--y)" };
    if (t === "medium") return { label: "Drift found", color: "var(--or)" };
  }
  return REF_STATUS[r.status] ?? { label: r.status, color: "var(--gr)" };
}

const PRECHECK_TEXT: Record<string, string> = {
  ready: "Can be analysed now",
  doi_not_found: "DOI not found on bioRxiv/medRxiv — check the DOI",
  v1_unavailable: "The first version's full text is not available — cannot be analysed",
  not_published_yet: "No published version yet — nothing to compare against",
  no_open_full_text: "The published version has no open full text — cannot be analysed",
  check_failed: "Could not reach bioRxiv / Europe PMC to check — you can still try",
};

function ReferenceRow({ r, initialStatus }: { r: SelfcheckReferenceResult; initialStatus: OnDemandStatus | null }) {
  const st = refStatus(r);
  const ev = r.event;
  return (
    <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--gr3)", borderLeft: `3px solid ${st.color}` }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start", justifyContent: "space-between", marginBottom: ev ? 10 : 0 }}>
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--wh2)", lineHeight: 1.6, flex: 1, wordBreak: "break-word" }}>{r.line}</div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
          {r.matched_by === "title" && (
            <span
              className={r.needs_confirmation ? "specimen specimen-y" : "specimen"}
              title={`Matched by title (${Math.round((r.title_coverage ?? 0) * 100)}% of the title's words found in your line)`}
            >
              {r.needs_confirmation ? "title match — please confirm" : "matched by title"}
            </span>
          )}
          <ColorBadge color={st.color}>{st.label}</ColorBadge>
        </div>
      </div>

      {r.detail && r.status !== "drift_found" && (
        <div className="specimen" style={{ marginTop: 6, color: "var(--gr)" }}>{r.detail}</div>
      )}

      {r.matched_by === "title" && ev && (
        <div style={{ fontSize: 12, color: "var(--gr)", marginBottom: 8 }}>
          Matched paper: <em>{(r.title_field === "published_title" ? ev.published_title : ev.preprint_title) ?? ev.preprint_doi}</em>
        </div>
      )}

      {r.status === "not_in_library" && r.doi && <NotInLibrary doi={r.doi} pre={r.precheck ?? null} initialStatus={initialStatus} />}

      {ev && <EventPanel ev={ev} />}

      {r.status === "no_match" && (r.suggestions?.length ?? 0) > 0 && <Suggestions r={r} />}
    </div>
  );
}

// A cited bioRxiv/medRxiv preprint the library does not have yet: the pre-check result, and "Analyse now" when the
// pre-check says the analysis can succeed (no pre-check = more than 30 such DOIs; the analysis itself re-checks).
function NotInLibrary({ doi, pre, initialStatus }: { doi: string; pre: SelfcheckPrecheck | null; initialStatus: OnDemandStatus | null }) {
  const canAnalyse = !pre || pre.status === "ready" || pre.status === "check_failed";
  return (
    <>
      {pre && (
        <div className="specimen" style={{ marginTop: 6, color: pre.status === "ready" ? "var(--grn)" : "var(--gr)" }}>
          {PRECHECK_TEXT[pre.status] ?? pre.status}
          {pre.status === "ready" && pre.published_doi ? ` (published as ${pre.published_doi}; usually takes 2–4 minutes)` : ""}
        </div>
      )}
      {canAnalyse && <AnalyzeNow doi={doi} initialStatus={initialStatus} />}
    </>
  );
}

type RefEvent = NonNullable<SelfcheckReferenceResult["event"]>;

// "Did you mean …?" for a line that matched nothing clearly (short or partial input): click a candidate to see it.
function Suggestions({ r }: { r: SelfcheckReferenceResult }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div style={{ marginTop: 8 }}>
      <div className="specimen" style={{ marginBottom: 6 }}>did you mean one of these?</div>
      {r.suggestions!.map((sg, k) => {
        const title = (sg.title_field === "published_title" ? sg.event.published_title : sg.event.preprint_title) ?? sg.event.preprint_doi;
        return (
          <div key={sg.event.event_id} style={{ marginBottom: 6 }}>
            <button
              className="cd-btn"
              style={{ padding: "6px 10px", fontSize: 12, textAlign: "left", width: "100%" }}
              onClick={() => setOpen(open === k ? null : k)}
            >
              {open === k ? "▾" : "▸"} {title}
            </button>
            {open === k && <EventPanel ev={sg.event} />}
          </div>
        );
      })}
    </div>
  );
}

function EventPanel({ ev }: { ev: RefEvent }) {
  return (
    <div style={{ background: "var(--bk3)", padding: "10px 12px" }}>
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap", marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr)" }}>{ev.preprint_doi}</div>
          <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--bl)" }}>↳ {ev.published_doi}</div>
        </div>
        <SeverityPair abstractClass={ev.abstract_class} fulltextTier={ev.fulltext_tier} />
        <VerificationBadge status={ev.review_status} />
        <Link href={`/event/${ev.event_id}`} className="cd-btn" style={{ padding: "6px 12px", fontSize: 11 }}>Event →</Link>
      </div>
      {ev.drift_summary && <div style={{ fontSize: 13, fontWeight: 300, color: "var(--gr)", lineHeight: 1.6, marginBottom: 8 }}>{ev.drift_summary}</div>}
      {ev.changes.map((c, j) => (
        <div key={j} style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2, background: "var(--gr3)", marginTop: 6 }}>
          <div style={{ background: "var(--bk2)", padding: "8px 10px" }}>
            <div className="specimen specimen-r" style={{ marginBottom: 4 }}>preprint (superseded)</div>
            <div style={{ fontSize: 13, color: "var(--wh2)", lineHeight: 1.55 }}>{c.preprint_text}</div>
          </div>
          <div style={{ background: "var(--bk2)", padding: "8px 10px" }}>
            <div className="specimen specimen-g" style={{ marginBottom: 4 }}>published (current)</div>
            <div style={{ fontSize: 13, color: "var(--wh2)", lineHeight: 1.55 }}>{c.published_text}</div>
          </div>
          {(c.change_description || c.severity_tier || c.root_cause) && (
            <div style={{ gridColumn: "1 / -1", background: "var(--bk2)", padding: "6px 10px", display: "flex", gap: 10, flexWrap: "wrap" }}>
              {c.severity_tier && <span className="specimen" style={{ color: FULLTEXT_TIER_COLOR[c.severity_tier] }}>full text: {c.severity_tier}</span>}
              {c.root_cause && <span className="specimen specimen-b">{labelOf(ROOT_CAUSE_LABEL, c.root_cause)}</span>}
              {c.change_description && <span className="specimen">{c.change_description}</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// Live progress of an on-demand analysis: steps done / total, the current step, the step log, elapsed time.
function OnDemandProgress({ progress, since }: { progress: SelfcheckAnalyzeResponse["progress"]; since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = Math.max(0, Math.round((now - since) / 1000));
  const total = progress?.steps_total ?? 0;
  const done = Math.min(progress?.steps_done ?? 0, total);
  const log = progress?.log ?? [];
  return (
    <div style={{ width: "100%", marginTop: 6, padding: "10px 12px", border: "1px solid var(--gr3)", background: "var(--bk2)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 6 }}>
        <span className="specimen specimen-b" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span className="cd-spinner" /> {progress?.line || "starting the analysis…"}
        </span>
        <span className="specimen">{total ? `step ${done}/${total} · ` : ""}{elapsed}s</span>
      </div>
      {total > 0 && (
        <div style={{ height: 4, background: "var(--gr3)", marginBottom: 8 }}>
          <div style={{ width: `${(100 * done) / total}%`, height: "100%", background: "var(--bl)", transition: "width .4s" }} />
        </div>
      )}
      {log.length > 0 && (
        <ol style={{ margin: 0, paddingLeft: 0, listStyle: "none", fontFamily: "var(--mono)", fontSize: 11, lineHeight: 1.7, color: "var(--gr)" }}>
          {log.map((l, i) => (
            <li key={i}>
              <span style={{ color: l.done ? "var(--grn)" : "var(--gr2)" }}>{l.done ? "✓" : "…"}</span>{" "}
              <span style={{ color: "var(--gr2)" }}>{l.t}s</span> {l.line}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// "Analyse now" for a preprint that is not in the library: starts the on-demand analysis, then polls every 3 s and
// shows each step as it finishes (fetch -> claim extraction x2 -> drift analysis -> verification -> saved).
function AnalyzeNow({ doi, initialStatus }: { doi: string; initialStatus: OnDemandStatus | null }) {
  const [status, setStatus] = useState<OnDemandStatus | null>(initialStatus);
  const [eventId, setEventId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<SelfcheckAnalyzeResponse["progress"]>(null);
  const [since, setSince] = useState<number>(() => Date.now());

  const needsPoll = status === "running" || (status === "done" && !eventId);

  useEffect(() => {
    if (!needsPoll) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await getSelfcheckAnalyze(doi);
        if (cancelled) return;
        setStatus(r.status);
        if (r.progress) setProgress(r.progress);
        if (r.progress?.log?.length && r.updated_at) {
          // anchor the elapsed clock to the server's start time (the run may have started before this page opened)
          const last = r.progress.log[r.progress.log.length - 1];
          setSince(Date.parse(r.updated_at) - last.t * 1000);
        }
        if (r.event_id) setEventId(r.event_id);
        if (r.error) setError(r.error);
      } catch (e) {
        if (!cancelled) setError(bffErrorMessage(e));
      }
    };
    if (status === "done") void tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [doi, needsPoll, status]);

  async function start() {
    setError(null);
    setProgress(null);
    setSince(Date.now());
    try {
      const r = await postSelfcheckAnalyze(doi);
      setStatus(r.status);
      if (r.event_id) setEventId(r.event_id);
      if (r.error) setError(r.error);
    } catch (e) {
      setError(bffErrorMessage(e));
    }
  }

  return (
    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 8 }}>
      {(status === null || status === "unknown" || status === "failed") && (
        <button className="cd-btn cd-btn-blue" style={{ padding: "6px 12px", fontSize: 11 }} onClick={start}>
          {status === "failed" ? "↻ Retry analysis" : "▶ Analyse now"}
        </button>
      )}
      {status === "running" && <OnDemandProgress progress={progress} since={since} />}
      {status === "done" && eventId && (
        <Link href={`/event/${eventId}`} className="cd-btn cd-btn-green" style={{ padding: "6px 12px", fontSize: 11 }}>
          ✓ Analysis done — open event →
        </Link>
      )}
      {status === "done" && !eventId && <span className="specimen specimen-g">analysis done — loading event…</span>}
      {status === "failed" && <span className="specimen" style={{ color: "var(--rd)" }}>analysis failed</span>}
      {status === "not_analysable" && <span className="specimen" style={{ color: "var(--gr)" }}>cannot be analysed</span>}
      {error && <span className="specimen" style={{ color: "var(--rd)" }}>{error}</span>}
    </div>
  );
}

// ── Citing sentence results ─────────────────────────────────────────────────
function SentenceResults({ res }: { res: SelfcheckSentencesResponse }) {
  return (
    <div>
      <div className="cd-panel" style={{ marginBottom: 14 }}>
        <div className="cd-panel-header">
          <span className="cd-panel-label">Citing sentences</span>
          <span className="specimen">{res.results.length} sentence(s) checked</span>
        </div>
        {res.summary && (
          <div style={{ padding: "10px 16px", display: "flex", gap: 18, flexWrap: "wrap" }}>
            <span style={{ fontSize: 13, color: "var(--wh2)" }}>
              <strong style={{ color: res.summary.uses_old_value ? "var(--rd)" : "var(--grn)", fontFamily: "var(--mono)" }}>
                {res.summary.uses_old_value}
              </strong>{" "}
              sentence(s) state a superseded value
            </span>
            <span style={{ fontSize: 13, color: "var(--wh2)" }}>
              <strong style={{ fontFamily: "var(--mono)" }}>{res.summary.matched}</strong> related to a revised preprint claim
            </span>
            <span style={{ fontSize: 13, color: "var(--wh2)" }}>
              <strong style={{ fontFamily: "var(--mono)" }}>{res.summary.no_match}</strong> not matched to any revision in the library
              (not a clearance — preprints outside the library are not checked here)
            </span>
            {!!res.n_ignored && (
              <span className="specimen" style={{ color: "var(--rd)" }}>{res.n_ignored} sentence(s) beyond the first {MAX_SENTENCES} were not checked</span>
            )}
          </div>
        )}
      </div>

      {res.results.map((r, i) => <SentenceResult key={i} r={r} />)}
    </div>
  );
}

function SentenceResult({ r }: { r: SelfcheckSentencesResponse["results"][number] }) {
  const [showWeak, setShowWeak] = useState(false);
  const weak = r.weak_matches ?? [];
  return (
    <div className="cd-panel" style={{ marginBottom: 14 }}>
      <div className="cd-panel-header" style={{ gap: 12 }}>
        <span style={{ fontSize: 14, color: "var(--wh2)", lineHeight: 1.5, fontWeight: 300 }}>&ldquo;{r.sentence}&rdquo;</span>
        <span className="specimen" style={{ flexShrink: 0 }}>{r.mode}</span>
      </div>
      {r.truncated && (
        <div className="specimen" style={{ padding: "6px 16px", color: "var(--rd)" }}>sentence longer than 2,000 characters — only the start was searched</div>
      )}
      {r.matches.length === 0 ? (
        <div style={{ padding: 16 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--gr2)", fontStyle: "italic", lineHeight: 1.6 }}>
            No revision in the library matches this sentence. This is not a clearance: sentences are only compared with
            revisions already in the library. If the preprint you cite is not in the library, paste its reference (with
            the DOI) in the reference list — a bioRxiv/medRxiv preprint can be analysed on demand there — then check this
            sentence again.
          </div>
          {weak.length > 0 && (
            <button className="cd-btn" style={{ marginTop: 10, padding: "4px 10px", fontSize: 11 }} onClick={() => setShowWeak((v) => !v)}>
              {showWeak ? "Hide" : "Show"} the nearest candidates (judged unrelated)
            </button>
          )}
          {showWeak && weak.map((m, j) => <SentenceMatch key={`${m.event_id}-w${j}`} m={m} rank={j + 1} />)}
        </div>
      ) : (
        <>
          <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--gr3)" }}>
            <span className="specimen">
              {r.judge === "unavailable"
                ? `${r.matches.length} candidate change(s) — the automatic check is unavailable, compare the texts yourself`
                : `${r.matches.length} related change(s) — ${r.match === "strong" ? "your sentence reports this claim" : "same finding, but check that your sentence cites this paper"}`}
            </span>
          </div>
          {r.matches.map((m, j) => <SentenceMatch key={`${m.event_id}-${j}`} m={m} rank={j + 1} />)}
        </>
      )}
    </div>
  );
}

const EVIDENCE_LABEL: Record<NonNullable<SelfcheckSentenceMatch["evidence"]>[number], string> = {
  author: "names the first author",
  doi: "cites the DOI",
  value: "states a value that changed",
  shared_value: "states a value of this claim",
};

const VERDICT_COLOR: Record<ValueVerdict, string> = {
  uses_old_value: "var(--rd)",
  mentions_both: "var(--or)",
  uses_current_value: "var(--grn)",
  unchanged_value: "var(--grn)",
  cannot_tell: "var(--gr)",
};

function verdictText(m: SelfcheckSentenceMatch): { label: string; text: string } {
  const vc = m.value_check;
  const old = vc?.old_values_found.join(", ") ?? "";
  const cur = vc?.new_values_found.join(", ") ?? "";
  switch (vc?.verdict) {
    case "uses_old_value":
      return {
        label: "Uses superseded value",
        text: `Your sentence states the superseded value ${old} — the published version revised it (see the current text below).`,
      };
    case "mentions_both":
      return {
        label: "Mentions both values",
        text: `Your sentence mentions the superseded value ${old} and the current value ${cur} — make sure it is framed as a revision.`,
      };
    case "uses_current_value":
      return { label: "Uses current value", text: `Your sentence already states the current value ${cur}.` };
    case "unchanged_value":
      return { label: "Value unchanged", text: "The numbers in your sentence are the same in both versions." };
    default:
      return {
        label: "Cannot tell",
        text: "Cannot tell: no number in your sentence matches either version. Compare the two texts below yourself.",
      };
  }
}

function SentenceMatch({ m, rank }: { m: SelfcheckSentenceMatch; rank: number }) {
  const verdict = m.value_check?.verdict ?? "cannot_tell";
  const { label, text } = verdictText(m);
  const color = VERDICT_COLOR[verdict] ?? "var(--gr)";
  const outdated = verdict === "uses_old_value" || verdict === "mentions_both";
  return (
    <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--gr3)", borderLeft: `3px solid ${color}` }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 13, fontWeight: 700, color: "var(--y)" }}>#{rank}</span>
        {m.strength && (
          <span className="specimen" style={{ color: m.strength === "strong" ? "var(--grn)" : m.strength === "possible" ? "var(--y)" : "var(--gr2)" }}>
            {m.strength === "strong" ? "close match" : m.strength === "possible" ? "possible match" : "weak match"}
          </span>
        )}
        {m.evidence && m.evidence.length > 0 && (
          <span className="specimen" style={{ color: "var(--gr2)" }}>
            {m.evidence.map((e) => EVIDENCE_LABEL[e]).join(" · ")}
          </span>
        )}
        {m.judge_reason && <span style={{ fontSize: 12, color: "var(--gr)", fontStyle: "italic" }}>{m.judge_reason}</span>}
        <ColorBadge color={color}>{label}</ColorBadge>
        <span style={{ fontSize: 13, color: "var(--wh2)" }}>{text}</span>
      </div>
      {outdated && <SuggestedFix m={m} />}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2, background: "var(--gr3)", marginBottom: 8 }}>
        <div style={{ background: "var(--bk2)", padding: "8px 10px" }}>
          <div className="specimen specimen-r" style={{ marginBottom: 4 }}>preprint (superseded)</div>
          <div style={{ fontSize: 13, color: "var(--wh2)", lineHeight: 1.55 }}>{m.preprint_text}</div>
        </div>
        <div style={{ background: "var(--bk2)", padding: "8px 10px" }}>
          <div className="specimen specimen-g" style={{ marginBottom: 4 }}>published (current)</div>
          <div style={{ fontSize: 13, color: "var(--wh2)", lineHeight: 1.55 }}>{m.published_text}</div>
        </div>
      </div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--gr)" }}>{m.preprint_doi} → {m.published_doi}</span>
        {m.severity_tier && <span className="specimen" style={{ color: FULLTEXT_TIER_COLOR[m.severity_tier] }}>full text: {m.severity_tier}</span>}
        {m.root_cause && <span className="specimen specimen-b">{labelOf(ROOT_CAUSE_LABEL, m.root_cause)}</span>}
        {m.change_description && <span className="specimen">{m.change_description}</span>}
        {m.score != null && <span className="specimen" style={{ color: "var(--gr2)" }}>score {m.score.toFixed(4)}</span>}
        <Link href={`/event/${m.event_id}`} className="specimen specimen-b" style={{ marginLeft: "auto", textDecoration: "none" }}>event →</Link>
      </div>
    </div>
  );
}

// For a sentence that states a superseded value: what the published version says now, ready to copy.
function SuggestedFix({ m }: { m: SelfcheckSentenceMatch }) {
  const [copied, setCopied] = useState(false);
  const cite = `${m.published_text} (published version: https://doi.org/${m.published_doi})`;
  async function copy() {
    try {
      await navigator.clipboard.writeText(cite);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div style={{ background: "var(--bk3)", padding: "8px 10px", marginBottom: 8, display: "flex", gap: 10, alignItems: "flex-start" }}>
      <div style={{ flex: 1 }}>
        <div className="specimen specimen-g" style={{ marginBottom: 4 }}>suggested fix — cite the published version</div>
        <div style={{ fontSize: 13, color: "var(--wh2)", lineHeight: 1.55 }}>{cite}</div>
      </div>
      <button className="cd-btn" style={{ padding: "4px 10px", fontSize: 11, flexShrink: 0 }} onClick={copy}>
        {copied ? "✓ copied" : "Copy"}
      </button>
    </div>
  );
}

// ── Already published paper ──────────────────────────────────────────────────
// The paper is read through the same MCP tools as the citation analysis (where does it cite each revised preprint in
// the library?) and judged with the same worker prompt.
function PublishedCheck() {
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<NoticeError | null>(null);
  const [res, setRes] = useState<SelfcheckPublishedResponse | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setRes(await postSelfcheckPublished(ref));
    } catch (e) {
      setError(noticeError(e));
      setRes(null);
    } finally {
      setBusy(false);
    }
  }

  const enabled = !busy && ref.trim().length > 0;
  return (
    <div>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 18 }}>
        <input
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          placeholder="DOI or PMCID of your published paper, e.g. PMC7097845"
          style={{ ...textareaStyle, minHeight: 0, width: 460, padding: "11px 14px" }}
        />
        <button onClick={run} disabled={!enabled} style={runBtnStyle(enabled)}>
          {busy ? "Reading your paper…" : "▶ Check paper"}
        </button>
        <span className="specimen">your paper must have open full text in Europe PMC (we read it there) · takes 10–60 s</span>
      </div>

      {error && <BffNotice title={error.title} message={error.message} />}

      {res && (
        <div className="cd-panel">
          <div className="cd-panel-header">
            <span className="cd-panel-label">{res.paper.title ?? res.paper.work_id ?? "Your paper"}</span>
            <span className="specimen">{res.paper.work_id ?? ""}</span>
          </div>
          <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--gr3)", fontSize: 13, color: "var(--wh2)" }}>
            {res.status === "not_found" && "This paper was not found in Europe PMC."}
            {res.status === "no_full_text" && "Europe PMC has no open full text for this paper, so it cannot be read."}
            {res.status === "checked" &&
              (res.n_library_preprints_cited
                ? <>Your paper cites <strong>{res.n_library_preprints_cited}</strong> preprint(s) whose claims were revised;{" "}
                    <strong style={{ color: res.summary?.relies_on_old_value ? "var(--rd)" : "var(--grn)" }}>{res.summary?.relies_on_old_value ?? 0}</strong>{" "}
                    of the checked values are used in their superseded form.</>
                : "Your paper cites none of the revised preprints in the library.")}
            {res.status === "checked" && !!res.not_in_library?.length && (
              <>
                {" "}It also cites <strong style={{ color: "var(--y)" }}>{res.not_in_library.length}</strong> bioRxiv/medRxiv
                preprint(s) that are not in the library yet — not checked; see below.
              </>
            )}
          </div>
          {res.results.map((r, i) => (
            <div key={i} style={{ padding: "12px 16px", borderBottom: "1px solid var(--gr3)" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
                <span style={{ fontSize: 14, color: "var(--wh)", flex: 1, minWidth: 260 }}>{r.event.preprint_title ?? r.event.preprint_doi}</span>
                {r.cites && <CitesBadge cites={r.cites} />}
                {r.role && r.role !== "unknown" && <span className="cd-badge">{labelOf(ROLE_LABEL, r.role)}</span>}
                {r.sentence && <VerifiedMark verified={r.sentence_verified ?? null} label={r.sentence_verified ? "sentence verbatim" : "sentence not found"} />}
                <Link href={`/event/${r.event.event_id}`} className="specimen specimen-b" style={{ textDecoration: "none" }}>event →</Link>
              </div>
              {r.status === "no_traceable_value" && <div className="specimen">{r.detail}</div>}
              {r.status === "not_judged" && <div className="specimen" style={{ color: "var(--rd)" }}>the paper could not be judged for this value</div>}
              {r.sentence && (
                <div style={{ fontSize: 13, color: "var(--wh2)", lineHeight: 1.6, marginBottom: 8 }}>&ldquo;{r.sentence}&rdquo;</div>
              )}
              {r.old && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2, background: "var(--gr3)", marginBottom: 6 }}>
                  <div style={{ background: "var(--bk2)", padding: "8px 10px" }}>
                    <div className="specimen specimen-r" style={{ marginBottom: 4 }}>preprint (superseded)</div>
                    <div style={{ fontSize: 13, color: "var(--wh2)", lineHeight: 1.55 }}>{r.old}</div>
                  </div>
                  <div style={{ background: "var(--bk2)", padding: "8px 10px" }}>
                    <div className="specimen specimen-g" style={{ marginBottom: 4 }}>published (current)</div>
                    <div style={{ fontSize: 13, color: "var(--wh2)", lineHeight: 1.55 }}>{r.new}</div>
                  </div>
                </div>
              )}
              {r.reason && <div className="specimen">{r.relayed_by ? `via ${r.relayed_by} · ` : ""}{r.reason}</div>}
            </div>
          ))}
          {!!res.not_in_library?.length && (
            <>
              <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--gr3)" }}>
                <span className="specimen specimen-y">cited preprints not in the library</span>
                <span className="specimen" style={{ marginLeft: 10 }}>
                  analyse one, then check your paper again to see how you cite it
                </span>
              </div>
              {res.not_in_library.map((p) => (
                <div key={p.doi} style={{ padding: "12px 16px", borderBottom: "1px solid var(--gr3)", borderLeft: "3px solid var(--y)" }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "center", justifyContent: "space-between" }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--wh2)", wordBreak: "break-word" }}>
                      {p.precheck?.title ? `${p.precheck.title} · ` : ""}doi:{p.doi}
                    </span>
                    <ColorBadge color="var(--y)">Not in library</ColorBadge>
                  </div>
                  <NotInLibrary doi={p.doi} pre={p.precheck} initialStatus={res.not_in_library_status?.[p.doi] ?? null} />
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
