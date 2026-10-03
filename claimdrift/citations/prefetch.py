"""Program-side pre-screen (§3.5 step 1, no LLM).

For the preprint DOI and the published DOI separately: Europe PMC `CITES:<id>_<src> AND ("<old value>" OR <spellings>)`,
cursor-paged; full texts downloaded in parallel and cached; the sentences with the old value extracted; deterministic
flags set:
  F1  the old value is not in (or next to) a sentence that cites the target via the reference list
      (possibly attributed to another paper that relayed it)
  F2  the old value appears only in a table row or list
  F3  the same sentence states both the old and the current value
`since` restricts to citing papers first published on/after that date (incremental re-run, P1.7); `exclude` skips
papers already judged.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import time

from .. import config
from . import epmc
from .terms import any_regex, query_variants


def spellings(terms: list[str]) -> list[str]:
    out: list[str] = []
    for t in terms:
        out += [v for v in query_variants(t) if v not in out]
    return out


def flags_for(sents: list[str], attributed: set[str], pat: re.Pattern, cur_terms: list[str]) -> list[str]:
    flags = []
    if not any(pat.search(a) for a in attributed):
        flags.append("F1")
    if all(len(s) > 600 or s.count("|") > 2 or len(re.findall(r"\d+\.\d+", s)) > 6 for s in sents):
        flags.append("F2")
    if cur_terms and any(all(c in s for c in cur_terms) for s in sents):
        flags.append("F3")
    return flags


def screen_works(tools: epmc.CitationTools, hits: dict[str, dict], target: dict, terms: list[str], source: str,
                 workers: int = config.PREFETCH_WORKERS, counts: dict | None = None) -> dict[str, dict]:
    """Download full texts of the hit papers and keep those whose text really contains the old value. `counts`, if
    given, is incremented with screened / no_full_text (download failed or a manual .missing record) / no_sentence."""
    ids = list(hits)
    with cf.ThreadPoolExecutor(workers) as ex:
        list(ex.map(tools.fetch, ids))
    pat = any_regex(terms)
    cur_terms = re.findall(r"\d+\.\d+", (target.get("drift") or {}).get("current_claim") or "")[:2]
    cands, n_missing, n_nosent = {}, 0, 0
    for wid in ids:
        t = tools.full_text(wid)
        if not t:
            n_missing += 1
            continue
        sents = [s for s in epmc.sentences(t) if pat.search(s)]
        if not sents:
            n_nosent += 1
            continue
        cit = tools.get_citation_sentences(wid)
        attributed = {s["with_neighbours"] for s in cit.get("sentences", [])} if cit.get("reference_matched") else set()
        h = hits[wid]
        cands[wid] = {"work_id": wid, "title": h.get("title"), "date": h.get("date"), "journal": h.get("journal"),
                      "authors": h.get("authors"), "doi": h.get("doi"),
                      "cites_versions": sorted(h.get("cites_target_version") or []),
                      "sentences": [s[:500] for s in sents[:3]], "flags": flags_for(sents, attributed, pat, cur_terms),
                      "reference_matched": bool(cit.get("reference_matched")), "source": source}
    if counts is not None:
        counts["screened"] = counts.get("screened", 0) + len(ids)
        counts["no_full_text"] = counts.get("no_full_text", 0) + n_missing
        counts["no_sentence"] = counts.get("no_sentence", 0) + n_nosent
    return cands


def list_hits(tools: epmc.CitationTools, terms: list[str], since: str | None = None,
              exclude: set[str] | frozenset = frozenset()) -> tuple[dict[str, dict], dict]:
    """Step 1 of the pre-screen (metadata only, fast): every citing paper whose open full text Europe PMC finds the old
    value in -> ({work_id: hit}, stats). The stats record what Europe PMC reported against what was fetched, so a cut-off
    is visible: search_truncated (more hits than MAX_PAGES pages), no_open_full_text (hits without a PMC / PPR full text
    id, which no worker could read)."""
    variants = spellings(terms)
    hits: dict[str, dict] = {}
    per_kind, truncated, no_ft = {}, False, 0
    for kind in ("preprint", "published"):
        if kind not in tools.ids:
            continue
        res, total = epmc.search_all_counted(tools.cites_query(kind, variants, since))
        per_kind[kind] = {"hit_count": total, "fetched": len(res)}
        truncated |= len(res) < total
        for h in res:
            w = epmc.work_of(h)
            if not w["work_id"]:
                no_ft += 1
                continue
            if w["work_id"] in exclude:
                continue
            versions = hits.setdefault(w["work_id"], {**w, "cites_target_version": []})["cites_target_version"]
            if kind not in versions:
                versions.append(kind)
    stats = {"query_spellings": variants, "per_version": per_kind, "search_truncated": truncated,
             "no_open_full_text": no_ft, "citing_works_with_value": len(hits), "since": since,
             "epmc_ids": {k: list(v) for k, v in tools.ids.items()}}
    return hits, stats


def prefetch(tools: epmc.CitationTools, target: dict, terms: list[str] | None = None, since: str | None = None,
             exclude: set[str] | frozenset = frozenset(), source: str = "prefetch") -> tuple[dict[str, dict], dict]:
    """Both steps in one call (local runs and small targets). Stepwise runs call list_hits once and screen_works in
    batches (Orchestra phase "prescreen")."""
    t0 = time.time()
    terms = terms or target["terms"]
    hits, stats = list_hits(tools, terms, since, exclude)
    counts: dict = {}
    cands = screen_works(tools, hits, target, terms, source, counts=counts)
    stats.update(counts, with_full_text_and_sentence=len(cands), secs=round(time.time() - t0, 1))
    return cands, stats
