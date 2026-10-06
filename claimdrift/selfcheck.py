"""Author self-check (P1.10): an author pastes citing sentences or a reference list from a manuscript; the system says
which cited preprint claims were revised in the published version.

  Path 1  reference list   DOIs matched exactly against drift_events (preprint or published DOI). Lines without a DOI are
                           matched by title: BM25 candidates over preprint_title + published_title, accepted by the share
                           of the candidate title's words that appear in the line (>= TITLE_ACCEPT -> match, >=
                           TITLE_POSSIBLE -> "please confirm"). bioRxiv/medRxiv DOIs with no event are `not_in_library`
                           and pre-checked (precheck_doi: does the DOI exist, is there a published version with open full
                           text?) so the author knows before starting an on-demand analysis whether it can succeed.
                           Every non-empty line is checked, however short (user decision 2026-10-02: no minimum length);
                           nothing is dropped silently.
  Path 2  citing sentence  the sentence is searched against the OLD text of every change (claim_diffs.preprint_text plus the
                           change description, prefixed with "<first author> et al. <title>") in the `drift_claims_search`
                           index; ranking = ELSER + BM25 fused by RRF. Retrieval only finds candidates: the top TOP_K
                           events, plus up to MAX_EXACT found by exact lookup of the first author, a DOI or a value
                           (first_author, *_values). One flash call per sentence then judges every candidate -- does the
                           sentence report this claim (yes / possible / no), and with the old value, the current one, both
                           or neither -- with the program's hints (author named, values in common) passed as hints only.
                           (2026-10-02: replaces the raw-score floor, which could not separate related from merely same-
                           topic sentences in a COVID-only library -- 55% of real citing sentences passed it, 56% of
                           unrelated ones did too -- and a short-lived set of hand-written evidence rules.)
Rejected events are never shown.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import threading

from . import config, es

INDEX = "drift_claims_search"
ONDEMAND_INDEX = "selfcheck_ondemand"  # status of on-demand analyses (survives BFF restarts)
RRF_K = 60
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>,;]+", re.I)
MAX_SENTENCES = 20
MAX_SENTENCE_CHARS = 2000
MAX_REFERENCE_CHARS = 100_000
MAX_PRECHECK = 30

# Path 2 candidates handed to the judge: the right event is among the top 10 for 92% of real citing sentences in a library
# padded with ~3,000 cross-field distractors (97% in the 80-change library; data/cases/experiments/selfcheck_distractors.py).
TOP_K = 10
MAX_EXACT = 5      # extra candidates found only by exact author / DOI / value lookup
MAX_WEAK = 3       # rejected candidates returned for display on request
JUDGE_WORKERS = 5  # parallel judge calls per request (<= MAX_SENTENCES sentences)

# Numbers that are not claim values: disease/strain/gene names with digits, years, confidence levels, citation markers,
# figure/table references, p-values. Removed from BOTH the claim texts and the author's sentence before values are read.
from .citations.terms import NOISE_PATTERNS as _NOISE  # noqa: E402 -- shared with the citation-target derivation
_NOISE_RE = re.compile("|".join(_NOISE))
_NUM = r"\d{1,3}(?:,\d{3})+(?:[.·]\d+)?|\d+(?:[.·]\d+)?"
_UNITS = {"%": "%", "percent": "%", "per cent": "%", "day": "days", "days": "days", "week": "weeks", "weeks": "weeks",
          "month": "months", "months": "months", "year": "years", "years": "years", "hour": "hours", "hours": "hours", "h": "hours",
          "min": "min", "minutes": "min", "patient": "patients", "patients": "patients", "case": "cases", "cases": "cases",
          "death": "deaths", "deaths": "deaths", "participant": "participants", "participants": "participants", "people": "people",
          "person": "people", "persons": "people", "individuals": "people", "children": "children", "adults": "adults",
          "sample": "samples", "samples": "samples", "fold": "fold", "times": "fold", "kg": "kg", "g": "g", "mg": "mg", "ml": "ml",
          "mm": "mm", "cm": "cm", "km": "km", "°c": "°c", "point": "points", "points": "points", "copies": "copies"}
_UNIT_RE = r"(?:\s*-?\s*)(" + "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True)) + r")(?![A-Za-z])"
_RANGE_RE = re.compile(rf"(?<![\w.·])({_NUM})\s*(?:to|and|or|–|—|-)\s*({_NUM}){_UNIT_RE}", re.I)
_VALUE_RE = re.compile(rf"(?<![\w.·])({_NUM})(?:{_UNIT_RE})?", re.I)

TITLE_ACCEPT, TITLE_POSSIBLE = 0.85, 0.6
SHORT_PRECISION = 0.8  # short/truncated reference lines: share of typed words that must belong to the paper
_STOP = {"a", "an", "the", "of", "in", "on", "for", "and", "or", "to", "with", "from", "by", "at", "as", "is", "are",
         "among", "between", "during", "its", "their", "after", "into", "versus", "vs"}


# ---------------------------------------------------------------- index (one doc per claim_diff)
def _meta_prefix(ev: dict) -> str:
    a, t = ev.get("first_author") or "", ev.get("preprint_title") or ""
    return f"{a} et al. {t}. " if (a or t) else ""


def index_event(ev: dict) -> int:
    es.delete_by_query(INDEX, {"term": {"event_id": ev["event_id"]}})
    if ev.get("review_status") == "rejected":
        return 0
    _ensure_fields()
    rows = []
    for i, d in enumerate(ev.get("claim_diffs") or []):
        body = " ".join(x for x in (d.get("preprint_text") or "", d.get("change_description") or "") if x).strip()
        if not body:
            continue
        txt = (_meta_prefix(ev) + body)[:2000]
        old, new = claim_values(d.get("preprint_text")), claim_values(d.get("published_text"))
        rows.append((f"{ev['event_id']}::{i}", {
            "event_id": ev["event_id"], "claim_diff_idx": i, "paper_id": ev.get("paper_id"),
            "preprint_doi": ev.get("preprint_doi"), "published_doi": ev.get("published_doi"),
            "first_author": (ev.get("first_author") or "").lower() or None,
            "old_values": sorted(old - new), "new_values": sorted(new - old), "shared_values": sorted(old & new),
            "root_cause": d.get("root_cause"), "severity_tier": d.get("severity_tier"),
            "text": txt, "text_bm": txt, "preprint_text": d.get("preprint_text"), "published_text": d.get("published_text"),
            "change_description": d.get("change_description")}))
    es.bulk_index(INDEX, rows)
    return len(rows)


# Evidence fields added 2026-10-02 (the mapping is dynamic: strict). Adding properties to an existing index is allowed on
# Serverless and on the local node; done once per process.
_EVIDENCE_FIELDS = {"first_author": {"type": "keyword"}, "old_values": {"type": "keyword"}, "new_values": {"type": "keyword"},
                    "shared_values": {"type": "keyword"}}
_fields_ok = False


def _ensure_fields() -> None:
    global _fields_ok
    if _fields_ok:
        return
    try:
        props = es.request("GET", f"{INDEX}/_mapping")[INDEX]["mappings"].get("properties", {})
    except es.ESError as e:
        if e.status == 404:  # index not created yet (setup-es creates it from elastic/mappings, which has the fields)
            return
        raise
    missing = {k: v for k, v in _EVIDENCE_FIELDS.items() if k not in props}
    if missing:
        es.request("PUT", f"{INDEX}/_mapping", {"properties": missing})
    _fields_ok = True


def rebuild_index() -> int:
    n = 0
    for ev in es.hits(config.INDICES["drift_events"], {"bool": {"must_not": [{"term": {"review_status": "rejected"}}],
                                                               "filter": [{"exists": {"field": "fulltext_severity.tier"}}]}}, size=5000):
        n += index_event(ev)
    return n


# ---------------------------------------------------------------- path 2: citing sentence
def _rank(text: str, mode: str = "hybrid", depth: int = 100) -> list[dict]:
    def run(q):
        return es.search(INDEX, {"size": depth, "query": q, "_source": {"excludes": ["text"]}})["hits"]["hits"]
    sem = run({"semantic": {"field": "text", "query": text}}) if mode in ("elser", "hybrid") else []
    lex = run({"match": {"text_bm": {"query": text}}}) if mode in ("bm25", "hybrid") else []
    score, src, raw = {}, {}, {}
    for name, ranking in (("elser", sem), ("bm25", lex)):
        for r, h in enumerate(ranking):
            score[h["_id"]] = score.get(h["_id"], 0.0) + 1.0 / (RRF_K + r + 1)
            src[h["_id"]] = h["_source"]
            raw.setdefault(h["_id"], {})[name] = round(h["_score"] or 0.0, 2)
    return [src[i] | {"rrf": round(score[i], 5), "elser_score": raw[i].get("elser", 0.0), "bm25_score": raw[i].get("bm25", 0.0)}
            for i in sorted(score, key=lambda i: -score[i])]


# ---------------------------------------------------------------- values stated in a text (normalisation only)
def _norm_num(n: str) -> str:
    n = n.replace(",", "").replace("·", ".")
    return n.rstrip("0").rstrip(".") if "." in n else n


def _fmt(n: str, unit: str) -> str:
    n = _norm_num(n)
    return n + unit if unit == "%" else f"{n} {unit}" if unit else n


def claim_values(text: str | None) -> set[str]:
    """The quantities a text states, as "number[ unit]" keys: thousands separators removed, middle dots read as decimal
    points, trailing zeros dropped, units normalised ("5·8 days" -> "5.8 days", "50.7 %" -> "50.7%", "29,500" -> "29500").
    Digits that are not quantities (_NOISE: names such as COVID-19, years, "95% CI", citation and figure numbers) are removed
    first; a range or pair shares the unit that follows it ("9 to 14 days" -> both in days). Used for hints and candidate
    lookup only -- whether a sentence refers to a change is decided by the model (judge_candidates)."""
    s = _NOISE_RE.sub(" ", (text or "").replace(" ", " "))
    out: set[str] = set()
    for m in _RANGE_RE.finditer(s):
        u = _UNITS[m.group(3).lower()]
        out.update((_fmt(m.group(1), u), _fmt(m.group(2), u)))
    for m in _VALUE_RE.finditer(_RANGE_RE.sub(" ", s)):
        out.add(_fmt(m.group(1), _UNITS[m.group(2).lower()] if m.group(2) else ""))
    return out


def _found(sentence_values: set[str], claim_keys: set[str]) -> set[str]:
    """Claim values the sentence states: same number and unit, or the same number written without its unit."""
    out = set(sentence_values & claim_keys)
    bare = {v for v in sentence_values if " " not in v and not v.endswith("%")}
    out.update(c for c in claim_keys if c.split(" ")[0] in bare)
    return out


def old_or_new(sentence: str, preprint_text: str | None, published_text: str | None, sentence_values: set[str] | None = None) -> dict:
    """Numeric comparison: does the sentence state a value found only in the old text, only in the new one, both, or one
    kept in both versions? A hint for the judge and the fallback when the judge is unavailable."""
    s = claim_values(sentence) if sentence_values is None else sentence_values
    old, new = claim_values(preprint_text), claim_values(published_text)
    old_only, new_only = _found(s, old - new), _found(s, new - old)
    if old_only and new_only:
        verdict = "mentions_both"
    elif old_only:
        verdict = "uses_old_value"
    elif new_only:
        verdict = "uses_current_value"
    else:
        verdict = "unchanged_value" if _found(s, old & new) else "cannot_tell"
    return {"verdict": verdict, "old_values_found": sorted(old_only), "new_values_found": sorted(new_only)}


# ---------------------------------------------------------------- candidates
def _exact_candidates(sentence: str, values: set[str]) -> list[dict]:
    """Changes the sentence names directly -- first author (any capitalised word), a DOI, or one of the change's values --
    by exact field lookup, so they reach the judge even when the semantic ranking buries them."""
    caps = sorted({w.lower() for w in re.findall(r"[A-Z][A-Za-z'\-]+", sentence)})
    dois = sorted({d.lower().rstrip(".") for d in DOI_RE.findall(sentence)})
    should: list[dict] = []
    if caps:
        should.append({"terms": {"first_author": caps}})
    if values:
        should += [{"terms": {f: sorted(values)}} for f in ("old_values", "new_values", "shared_values")]
    if dois:
        should += [{"terms": {f: dois}} for f in ("preprint_doi", "published_doi")]
    if not should:
        return []
    r = es.search(INDEX, {"size": 200, "query": {"bool": {"should": should, "minimum_should_match": 1}}, "_source": {"excludes": ["text"]}})
    return [h["_source"] for h in r["hits"]["hits"]]


def hints(sentence: str, h: dict, values: set[str]) -> tuple[list[str], dict]:
    """-> (hint kinds, numeric comparison). author | doi | value (a value of only one version) | shared_value."""
    kinds = []
    sur = h.get("first_author") or ""
    if sur and re.search(rf"(?<![A-Za-z]){re.escape(sur)}(?![A-Za-z])", sentence, re.I):
        kinds.append("author")
    low = sentence.lower()
    if any(h.get(k) and h[k].lower() in low for k in ("preprint_doi", "published_doi")):
        kinds.append("doi")
    vc = old_or_new(sentence, h.get("preprint_text"), h.get("published_text"), values)
    if vc["old_values_found"] or vc["new_values_found"]:
        kinds.append("value")
    elif vc["verdict"] == "unchanged_value":
        kinds.append("shared_value")
    return kinds, vc


def candidates(sentence: str, mode: str = "hybrid") -> list[dict]:
    """The top TOP_K events of the retrieval ranking plus up to MAX_EXACT events found only by exact lookup (author / DOI
    first, then the number of matching values). One change per event: the best-ranked one, or for exact-only events the one
    with the most hints."""
    values = claim_values(sentence)
    out: dict[str, dict] = {}
    for h in _rank(sentence, mode):
        if h["event_id"] in out:
            continue
        kinds, vc = hints(sentence, h, values)
        out[h["event_id"]] = h | {"hints": kinds, "numeric": vc, "retrieval_rank": len(out) + 1}
        if len(out) >= TOP_K:
            break
    extra: dict[str, dict] = {}
    for h in _exact_candidates(sentence, values):
        if h["event_id"] in out:
            continue
        kinds, vc = hints(sentence, h, values)
        if not kinds:
            continue
        key = ("author" in kinds or "doi" in kinds, "value" in kinds, len(vc["old_values_found"]) + len(vc["new_values_found"]))
        cur = extra.get(h["event_id"])
        if cur is None or key > cur["_key"]:
            extra[h["event_id"]] = h | {"hints": kinds, "numeric": vc, "retrieval_rank": None, "rrf": 0.0, "elser_score": 0.0,
                                        "bm25_score": 0.0, "_key": key}
    for h in sorted(extra.values(), key=lambda h: h["_key"], reverse=True)[:MAX_EXACT]:
        out[h["event_id"]] = {k: v for k, v in h.items() if k != "_key"}
    return list(out.values())


# ---------------------------------------------------------------- judgement (one flash call per sentence)
JUDGE_SYSTEM = """You check one sentence from an author's manuscript against candidate claims. Each candidate is a claim from a
preprint that the authors revised when the paper was published: the preprint (old) text, the published (current) text, and
what changed.

For EACH candidate decide:
- refers: "yes" if the sentence reports this paper's claim (this finding, in any wording or rounding; the paper may be cited
  by name or only by a number marker such as [12]); "possible" if the sentence reports the same finding but nothing in it
  settles whether it is this paper's; "no" if it is about something else, about another study, or only on the same topic.
- value_use (for yes / possible): "uses_old_value" (states the preprint's value or wording), "uses_current_value",
  "mentions_both", "unchanged_value" (states only what both versions say), or "cannot_tell".
Program hints (first author named, values in common) can be wrong; decide from the texts. Several candidates may be "yes".
Return ONLY JSON: {"judgements": [{"id": "c1", "refers": "yes|possible|no", "value_use": "...", "reason": "<15 words>"}]}"""

_VALUE_USE = {"uses_old_value", "uses_current_value", "mentions_both", "unchanged_value", "cannot_tell"}


def _render(cands: list[dict]) -> str:
    rows = []
    for i, c in enumerate(cands, 1):
        rows.append(f"### c{i}  ({c.get('first_author') or 'unknown'} et al.; hints: {', '.join(c['hints']) or 'none'})\n"
                    f"preprint (old): {(c.get('preprint_text') or '')[:700]}\n"
                    f"published (current): {(c.get('published_text') or '')[:700]}\n"
                    f"what changed: {(c.get('change_description') or '')[:300]}")
    return "\n\n".join(rows)


def judge_candidates(sentence: str, cands: list[dict], backend=None) -> dict[int, dict] | None:
    """-> {candidate index: {"refers", "value_use", "reason"}}, or None when the model call fails or returns no JSON."""
    if not cands:
        return {}
    from . import llm
    try:
        backend = backend or llm.flash()
        r = backend.chat([{"role": "system", "content": JUDGE_SYSTEM},
                          {"role": "user", "content": f"Sentence: {sentence}\n\nCandidates:\n\n{_render(cands)}"}])
    except Exception:  # noqa: BLE001
        return None
    out = (llm.extract_json(r["content"]) or {}).get("judgements")
    if not isinstance(out, list):
        return None
    res = {}
    for j in out:
        m = re.fullmatch(r"c(\d+)", str(j.get("id") or ""))
        if m and 1 <= int(m.group(1)) <= len(cands):
            refers = j.get("refers") if j.get("refers") in ("yes", "possible", "no") else "no"
            vu = j.get("value_use") if j.get("value_use") in _VALUE_USE else "cannot_tell"
            res[int(m.group(1)) - 1] = {"refers": refers, "value_use": vu, "reason": str(j.get("reason") or "")[:200]}
    return res


_STRENGTH = {"yes": "strong", "possible": "possible", "no": "weak"}
_TIER = {"strong": 2, "possible": 1, "weak": 0}


def check_sentence(sentence: str, mode: str = "hybrid", backend=None) -> dict:
    """Retrieval finds candidates; one model call decides which the sentence refers to. matches = every candidate judged
    yes / possible (no cap); weak_matches = the MAX_WEAK best-ranked others, for display on request. If the model call
    fails, the top MAX_WEAK candidates are returned as unconfirmed "possible" matches (judge = unavailable)."""
    truncated = len(sentence) > MAX_SENTENCE_CHARS
    sentence = sentence[:MAX_SENTENCE_CHARS]
    cands = candidates(sentence, mode)
    verdicts = judge_candidates(sentence, cands, backend)
    rows = []
    for i, c in enumerate(cands):
        if verdicts is None:
            v = {"refers": "possible" if i < MAX_WEAK else "no", "value_use": c["numeric"]["verdict"], "reason": "judge unavailable"}
        else:
            v = verdicts.get(i, {"refers": "no", "value_use": "cannot_tell", "reason": "not judged"})
        rows.append({"event_id": c["event_id"], "paper_id": c.get("paper_id"), "preprint_doi": c.get("preprint_doi"),
                     "published_doi": c.get("published_doi"), "claim_diff_idx": c.get("claim_diff_idx"),
                     "preprint_text": c.get("preprint_text"), "published_text": c.get("published_text"),
                     "change_description": c.get("change_description"), "severity_tier": c.get("severity_tier"),
                     "root_cause": c.get("root_cause"), "score": c.get("rrf", 0.0), "elser_score": c.get("elser_score", 0.0),
                     "bm25_score": c.get("bm25_score", 0.0), "retrieval_rank": c.get("retrieval_rank"),
                     "strength": _STRENGTH[v["refers"]], "evidence": c["hints"], "judge_reason": v["reason"],
                     "value_check": {"verdict": v["value_use"], "old_values_found": c["numeric"]["old_values_found"],
                                     "new_values_found": c["numeric"]["new_values_found"]}})
    rows.sort(key=lambda m: (_TIER[m["strength"]], -(m["retrieval_rank"] or TOP_K + 1)), reverse=True)
    relevant = [m for m in rows if m["strength"] != "weak"]
    best = "strong" if any(m["strength"] == "strong" for m in relevant) else "possible" if relevant else "no_match"
    return {"sentence": sentence, "mode": mode, "truncated": truncated, "match": best, "matches": relevant,
            "weak_matches": [m for m in rows if m["strength"] == "weak"][:MAX_WEAK],
            "judge": "unavailable" if verdicts is None else "flash", "n_candidates": len(cands)}


def check_sentences(sentences: list[str], mode: str = "hybrid") -> dict:
    from . import llm
    sents = [s.strip() for s in sentences if isinstance(s, str) and s.strip()]
    try:
        backend = llm.flash()
    except Exception:  # noqa: BLE001  (no API key: every sentence falls back to unconfirmed candidates)
        backend = None
    with cf.ThreadPoolExecutor(JUDGE_WORKERS) as ex:
        results = list(ex.map(lambda s: check_sentence(s, mode=mode, backend=backend), sents[:MAX_SENTENCES]))
    return {"results": results, "n_submitted": len(sents), "n_checked": len(results),
            "n_ignored": max(0, len(sents) - MAX_SENTENCES),
            "summary": {"uses_old_value": sum(1 for r in results if any(m["value_check"]["verdict"] in ("uses_old_value", "mentions_both")
                                                                        for m in r["matches"])),
                        "matched": sum(1 for r in results if r["match"] != "no_match"),
                        "no_match": sum(1 for r in results if r["match"] == "no_match")},
            "judge_usage": backend.usage() if backend is not None else None}


# ---------------------------------------------------------------- path 1: reference list
def _events_by_doi(dois: list[str]) -> dict[str, dict]:
    if not dois:
        return {}
    q = {"bool": {"should": [{"terms": {"preprint_doi": dois}}, {"terms": {"published_doi": dois}}], "minimum_should_match": 1,
                  "must_not": [{"term": {"review_status": "rejected"}}]}}
    out = {}
    for ev in es.hits(config.INDICES["drift_events"], q, size=500):
        for k in ("preprint_doi", "published_doi"):
            if ev.get(k):
                out[ev[k].lower()] = ev
    return out


def _summary(ev: dict) -> dict:
    return {"event_id": ev["event_id"], "preprint_doi": ev.get("preprint_doi"), "published_doi": ev.get("published_doi"),
            "preprint_title": ev.get("preprint_title"), "published_title": ev.get("published_title"),
            "drift_summary": ev.get("drift_summary"), "fulltext_tier": (ev.get("fulltext_severity") or {}).get("tier"),
            "abstract_class": (ev.get("abstract_severity") or {}).get("class"), "review_status": ev.get("review_status"),
            "changes": [{k: d.get(k) for k in ("preprint_text", "published_text", "change_description", "severity_tier", "root_cause")}
                        for d in ev.get("claim_diffs") or []]}


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in _STOP]


def title_coverage(line: str, title: str) -> float:
    """Share of the title's content words that appear in the reference line (author names, journal and year in the line
    do not lower it, unlike a query-side minimum_should_match)."""
    tw, lw = _words(title), set(_words(line))
    return round(sum(w in lw for w in tw) / len(tw), 3) if tw else 0.0


def line_precision(line: str, title: str, author: str | None) -> tuple[float, int]:
    """For short or truncated input ("Guan W, et al. Clinical characteristics of 201"): share of the words the author typed
    that belong to this paper (title words, the first author's surname; the last word may be cut off, so a prefix of a
    title word counts). -> (precision, number of matched title words)."""
    lw = [w for w in _words(line) if w not in ("et", "al") and len(w) > 1]
    if not lw:
        return 0.0, 0
    tw = set(_words(title))
    au = (author or "").lower()
    hit = title_hits = 0
    for i, w in enumerate(lw):
        if w in tw:
            hit, title_hits = hit + 1, title_hits + 1
        elif au and w == au:
            hit += 1
        elif i == len(lw) - 1 and len(w) >= 3 and any(t.startswith(w) for t in tw):
            hit, title_hits = hit + 1, title_hits + 1
    return round(hit / len(lw), 3), title_hits


def title_candidates(line: str, size: int = 10) -> list[dict]:
    """Candidate events for a reference line without DOI, best first, with their scores."""
    caps = [w for w in re.findall(r"[A-Z][a-zA-Z\-]+", line)]
    r = es.search(config.INDICES["drift_events"], {"size": size, "query": {"bool": {
        "should": [{"match": {"preprint_title": line}}, {"match": {"published_title": line}},
                   {"terms": {"first_author": caps}}], "minimum_should_match": 1,
        "must_not": [{"term": {"review_status": "rejected"}}]}}})["hits"]["hits"]
    out = []
    for h in r:
        ev = h["_source"]
        best = None
        for field in ("preprint_title", "published_title"):
            t = ev.get(field) or ""
            if not t:
                continue
            cov = title_coverage(line, t)
            prec, n_title = line_precision(line, t, ev.get("first_author"))
            cand = {"event": ev, "title_field": field, "title_coverage": cov, "line_precision": prec, "title_words_matched": n_title,
                    "author_match": bool(ev.get("first_author")) and ev["first_author"].lower() in _words(line)}
            if best is None or (cov, prec) > (best["title_coverage"], best["line_precision"]):
                best = cand
        if best:
            out.append(best)
    out.sort(key=lambda c: (c["title_coverage"] >= TITLE_ACCEPT, c["line_precision"] * min(c["title_words_matched"], 4), c["title_coverage"]),
             reverse=True)
    return out


def classify_title(c: dict) -> str:
    """match | confirm | none. Full titles are judged by title coverage; short/truncated input by how much of what was
    typed belongs to the paper (all words typed must fit, at least two of them title words)."""
    if c["title_coverage"] >= TITLE_ACCEPT:
        return "match"
    if c["title_coverage"] >= TITLE_POSSIBLE:
        return "confirm"
    if c["line_precision"] >= SHORT_PRECISION and c["title_words_matched"] >= 2:
        return "confirm"
    return "none"


_precheck_cache: dict[str, dict] = {}
_precheck_lock = threading.Lock()


def precheck_doi(doi: str) -> dict:
    """Can an on-demand analysis of this bioRxiv/medRxiv DOI succeed? Network only (bioRxiv API + Europe PMC search), no LLM.
    status: ready | doi_not_found | v1_unavailable | not_published_yet | no_open_full_text | check_failed."""
    doi = doi.lower()
    with _precheck_lock:
        if doi in _precheck_cache:
            return _precheck_cache[doi]
    from .documents import _meta_from_details, _versions_module, EPMC, _epmc_get
    import urllib.parse
    try:
        V = _versions_module()
        server, history = V._fetch_history_sync(doi, None)
        if not history:
            out = {"status": "doi_not_found", "detail": "bioRxiv/medRxiv has no record of this DOI"}
        elif V.first_version_entry(history) is None:
            out = {"status": "v1_unavailable", "detail": "the first version's full text is not available"}
        else:
            meta = _meta_from_details(history)
            pub = meta["published_doi"]
            if not pub:
                out = {"status": "not_published_yet", "detail": "no published version is recorded for this preprint yet",
                       "title": meta["title"], "versions": meta["versions"]}
            else:
                q = urllib.parse.urlencode({"query": f'DOI:"{pub}"', "format": "json", "resultType": "lite"})
                res = _epmc_get(EPMC + "search?" + q).get("resultList", {}).get("result", [])
                pmcid = next((h.get("pmcid") for h in res if h.get("pmcid")), None)
                base = {"title": meta["title"], "published_doi": pub, "versions": meta["versions"], "server": server}
                out = base | ({"status": "ready", "detail": "can be analysed; usually takes 2-4 minutes"} if pmcid else
                              {"status": "no_open_full_text", "detail": "the published version has no open full text in Europe PMC"})
    except Exception as e:  # noqa: BLE001
        return {"status": "check_failed", "detail": f"{type(e).__name__}: {e}"[:200]}
    with _precheck_lock:
        _precheck_cache[doi] = out
    return out


def check_references(text: str, precheck: bool = True) -> dict:
    text = text or ""
    truncated = len(text) > MAX_REFERENCE_CHARS
    lines = [ln.strip() for ln in re.split(r"\n+", text[:MAX_REFERENCE_CHARS]) if ln.strip()]
    items = []
    for ln in lines:
        m = DOI_RE.search(ln)
        items.append({"line": ln[:400], "doi": m.group(0).rstrip(".)").lower() if m else None})
    by_doi = _events_by_doi([i["doi"] for i in items if i["doi"]])
    results = []
    for it in items:
        if it["doi"]:
            ev = by_doi.get(it["doi"])
            if ev:
                results.append(it | {"status": "drift_found", "matched_by": "doi", "event": _summary(ev)})
            elif it["doi"].startswith("10.1101/"):
                results.append(it | {"status": "not_in_library", "matched_by": None})
            else:
                results.append(it | {"status": "not_tracked", "matched_by": None,
                                     "detail": "not a bioRxiv/medRxiv preprint and not the published version of one in the library"})
            continue
        cands = title_candidates(it["line"])
        top = cands[0] if cands else None
        kind = classify_title(top) if top else "none"
        if kind != "none":
            results.append(it | {"status": "drift_found", "matched_by": "title", "title_coverage": top["title_coverage"],
                                 "title_field": top["title_field"], "needs_confirmation": kind == "confirm",
                                 "event": _summary(top["event"])})
        else:
            sugg = [c for c in cands if c["author_match"] or (c["title_words_matched"] >= 2 and c["title_coverage"] >= 0.25)][:3]
            results.append(it | {"status": "no_match", "matched_by": None, "title_coverage": top["title_coverage"] if top else 0.0,
                                 "detail": "no DOI, and no paper in the library clearly matches this line",
                                 "suggestions": [{"title_field": c["title_field"], "title_coverage": c["title_coverage"],
                                                  "event": _summary(c["event"])} for c in sugg]})
    nil = list(dict.fromkeys(r["doi"] for r in results if r["status"] == "not_in_library"))
    prechecks = {}
    if precheck and nil:
        with cf.ThreadPoolExecutor(8) as ex:
            prechecks = dict(zip(nil[:MAX_PRECHECK], ex.map(precheck_doi, nil[:MAX_PRECHECK])))
    for r in results:
        if r["status"] == "not_in_library":
            r["precheck"] = prechecks.get(r["doi"])
    tiers = [((r.get("event") or {}).get("fulltext_tier")) for r in results if r["status"] == "drift_found"]
    return {"n_lines": len(lines), "truncated": truncated, "results": results,
            "n_drift_found": len(tiers), "not_in_library": nil,
            "summary": {"drift_found": len(tiers), "significant_or_major": sum(t in ("significant", "major") for t in tiers),
                        "needs_confirmation": sum(1 for r in results if r.get("needs_confirmation")),
                        "not_in_library": len(nil), "not_tracked": sum(r["status"] == "not_tracked" for r in results),
                        "no_match": sum(r["status"] == "no_match" for r in results),
                        "skipped": sum(r["status"] == "skipped" for r in results)}}


# ---------------------------------------------------------------- path 3: an already published paper
PMCID_RE = re.compile(r"^(PMC|PPR)\d+$", re.I)
MAX_PUBLISHED_TARGETS = 8


def resolve_work(ref: str) -> dict:
    """PMCID / PPR id / DOI -> Europe PMC record {work_id, title, doi} (work_id None when there is no open full text)."""
    import urllib.parse
    from .citations import epmc
    ref = (ref or "").strip()
    m = DOI_RE.search(ref)
    if PMCID_RE.match(ref):
        q = f"PMCID:{ref.upper()}" if ref.upper().startswith("PMC") else f"EXT_ID:{ref.upper()} AND SRC:PPR"
    elif m:
        q = f'DOI:"{m.group(0).rstrip(".)")}"'
    else:
        raise ValueError(f"“{ref[:60]}” is not a DOI, PMCID or Europe PMC preprint id. Enter one of these, e.g. "
                         "10.1016/S2214-109X(20)30074-7, PMC7097845 or PPR123456.")
    res = epmc.get(epmc.EPMC + "search?" + urllib.parse.urlencode({"query": q, "format": "json", "resultType": "lite"}))
    hits = res.get("resultList", {}).get("result", [])
    if not hits:
        return {"work_id": None, "title": None, "doi": None, "detail": "not found in Europe PMC"}
    w = epmc.work_of(hits[0])
    return {"work_id": w["work_id"], "title": w["title"], "doi": w["doi"], "authors": w["authors"]}


def ref_list_text(xml: str) -> str:
    from .citations.epmc import plain
    i = xml.find("<ref-list")
    return plain(xml[i:] if i >= 0 else "").lower()


def cited_library_events(refs: str) -> list[dict]:
    """Library events whose preprint or published version appears in the paper's reference list (lower-cased plain
    text, ref_list_text)."""
    if not refs:
        return []
    out = []
    for ev in es.hits(config.INDICES["drift_events"], {"bool": {"must_not": [{"term": {"review_status": "rejected"}}],
                                                               "filter": [{"exists": {"field": "fulltext_severity.tier"}}]}}, size=5000):
        dois = [d.lower() for d in (ev.get("preprint_doi"), ev.get("published_doi")) if d]
        title = (ev.get("preprint_title") or "").lower()[:35]
        author = (ev.get("first_author") or "").lower()
        if any(d in refs for d in dois) or (author and title and author in refs and title in refs):
            out.append(ev)
    return out


def preprints_not_in_library(refs: str) -> list[str]:
    """bioRxiv/medRxiv DOIs in a paper's reference list (ref_list_text) with no event in the library, in order of first
    appearance. A version suffix (".../2020.02.06.20020974v2") is dropped: on-demand analysis takes the bare DOI."""
    dois = list(dict.fromkeys(re.sub(r"v\d+$", "", m.group(0).rstrip(".)")) for m in DOI_RE.finditer(refs or "")
                              if m.group(0).startswith("10.1101/")))
    known = _events_by_doi(dois)
    return [d for d in dois if d not in known]


def check_published(ref: str, client) -> dict:
    """Author self-check of an already published paper: which revised preprint claims in the library does it cite, and
    does it rely on the old value? Reads the paper through the SAME MCP tools as the citation job
    (get_reference_list, get_citation_sentences, search_in_work, verify_citing_quote) and judges with the same worker
    prompt (citations.orchestra.run_worker). The full text is read and cached by the tool service only. Cited
    bioRxiv/medRxiv preprints that are not in the library are listed with a pre-check (not_in_library), as in path 1."""
    from . import llm
    from .citations.access import WORKER_TOOLS, target_ref
    from .citations.orchestra import run_worker
    from .citations.targets import build_targets
    work = resolve_work(ref)
    if not work.get("work_id"):
        return {"paper": work, "status": "not_found", "results": []}
    refs = client.call("get_reference_list", {"work_id": work["work_id"]})
    if not refs.get("full_text"):
        return {"paper": work, "status": "no_full_text", "results": []}
    events = cited_library_events(refs.get("references") or "")
    # cited preprints the library does not have yet: pre-checked like path 1, so the author can start an on-demand analysis
    nil = preprints_not_in_library(refs.get("references") or "")
    with cf.ThreadPoolExecutor(8) as ex:
        prechecks = dict(zip(nil[:MAX_PRECHECK], ex.map(precheck_doi, nil[:MAX_PRECHECK])))
    not_in_library = [{"doi": d, "precheck": prechecks.get(d)} for d in nil]
    jobs = []
    for ev in events:
        targets = build_targets(ev, ev.get("paper_id") or "", ev.get("preprint_title") or "", ev.get("first_author") or "")
        if not targets:
            jobs.append((ev, None))
        jobs += [(ev, t) for t in targets]
    jobs = jobs[:MAX_PUBLISHED_TARGETS]

    def judge(job):
        ev, t = job
        base = {"event": _summary(ev)}
        if t is None:
            return base | {"status": "no_traceable_value",
                           "detail": "this preprint is cited, but its revision has no specific value to trace"}
        tools = client.bind(WORKER_TOOLS, **target_ref(t))
        line = f"- {work['work_id']} (flags: none; pre-screen sentence: not pre-screened -- author self-check of a published paper)"
        v = next((x for x in run_worker(t, [line], tools, llm.pro()) if x.get("work_id") == work["work_id"]), None)
        if v is None:
            return base | {"status": "not_judged", "old": t["drift"]["preprint_v1_claim"], "new": t["drift"]["current_claim"]}
        sent = str(v.get("sentence") or "")
        ok = bool(sent) and bool(client.call("verify_citing_quote", {**target_ref(t), "work_id": work["work_id"],
                                                                    "quote": sent}).get("verified"))
        return base | {"status": "judged", "old": t["drift"]["preprint_v1_claim"], "new": t["drift"]["current_claim"],
                       "cites": v.get("cites"), "role": v.get("role"), "sentence": sent, "sentence_verified": ok,
                       "relayed_by": v.get("relayed_by") or "", "reason": v.get("reason") or ""}

    with cf.ThreadPoolExecutor(4) as ex:
        results = list(ex.map(judge, jobs))
    return {"paper": work, "status": "checked", "n_library_preprints_cited": len(events), "results": results,
            "not_in_library": not_in_library,
            "summary": {"relies_on_old_value": sum(1 for r in results if r.get("cites") in ("superseded", "indirect")),
                        "judged": sum(1 for r in results if r.get("status") == "judged")}}


# ---------------------------------------------------------------- on-demand analysis status (persisted)
def ondemand_get(doi: str) -> dict | None:
    try:
        return es.source(ONDEMAND_INDEX, doi.lower())
    except es.ESError:
        return None


def ondemand_put(doi: str, doc: dict) -> None:
    # refresh=false: the status is read back by id (GET is real-time), and progress is written several times per run
    es.put(ONDEMAND_INDEX, doi.lower(), {"preprint_doi": doi.lower(), "updated_at": _now()} | doc, refresh="false")


def _now() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat()
