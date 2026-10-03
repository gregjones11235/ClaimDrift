"""Europe PMC access for the citation analysis (open full texts only). Ported from
data/cases/experiments/citation_agent.py (CitationTools); the on-disk full-text cache is shared with the experiments.

Scope note for every recall figure: only citing papers with an open full text in Europe PMC are visible. In the gold
set, 0 of 27 real users of a superseded value had it in their abstract, so abstract-level screening is not an option.
Europe PMC covers the life sciences, which matches the bioRxiv/medRxiv target domain.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request

from .. import config

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
_write_lock = threading.Lock()


FULLTEXT_TIMEOUT, FULLTEXT_TRIES = 20, 2  # per citing-paper full-text download (a failing paper costs at most ~42 s)


def get(url: str, js: bool = True, tries: int = 3, timeout: float = 60):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "claimdrift"}), timeout=timeout) as r:
                b = r.read()
            return json.loads(b) if js else b.decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def search(query: str, page_size: int = 25, cursor: str = "*") -> dict:
    return get(EPMC + "search?" + urllib.parse.urlencode({"query": query, "format": "json", "resultType": "lite",
                                                          "pageSize": page_size, "cursorMark": cursor}))


MAX_PAGES = 50  # x 100 = 5,000 hits per query; beyond that a run is reported as truncated (search_all_counted)


def search_all_counted(query: str, page_size: int = 100, max_pages: int = MAX_PAGES) -> tuple[list[dict], int]:
    """Every hit of a query, following the cursor, up to max_pages -> (hits, Europe PMC's total hitCount). The caller
    compares the two: fewer hits than hitCount means the result was cut off and must be reported as such."""
    out, cursor, total = [], "*", 0
    for _ in range(max_pages):
        r = search(query, page_size, cursor)
        total = max(total, int(r.get("hitCount") or 0))
        res = r.get("resultList", {}).get("result", [])
        out += res
        nxt = r.get("nextCursorMark")
        if len(res) < page_size or not nxt or nxt == cursor:
            break
        cursor = nxt
    return out, max(total, len(out))


def search_all(query: str, page_size: int = 100, max_pages: int = MAX_PAGES) -> list[dict]:
    """Every hit of a query, following the cursor (see search_all_counted for the truncation check)."""
    return search_all_counted(query, page_size, max_pages)[0]


def work_of(h: dict) -> dict:
    wid = h.get("pmcid") or (h.get("id") if h.get("source") == "PPR" else None)
    return {"work_id": wid, "title": (h.get("title") or "")[:200], "date": h.get("firstPublicationDate") or h.get("pubYear"),
            "journal": h.get("journalTitle") or h.get("journalAbbreviation") or ("preprint" if h.get("source") == "PPR" else None),
            "authors": h.get("authorString") or "", "doi": (h.get("doi") or "").lower() or None, "full_text_available": bool(wid)}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(])", text) if s.strip()]


def plain(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x)))


class CitationTools:
    """Reading tools over the citing papers of ONE target (a drifted preprint + its published version)."""

    def __init__(self, target: dict):
        self.t = target
        self.ids: dict[str, tuple[str, str]] = {}
        config.EPMC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for kind in ("preprint", "published"):
            doi = target.get(kind + "_doi")
            if not doi:
                continue
            res = get(EPMC + "search?" + urllib.parse.urlencode({"query": f'DOI:"{doi}"', "format": "json", "resultType": "lite"}))
            res = res.get("resultList", {}).get("result", [])
            if res:
                self.ids[kind] = (res[0]["source"], res[0]["id"])
        self._text: dict[str, str] = {}
        self._xml: dict[str, str] = {}

    # ---- full text --------------------------------------------------------------------------------------------
    def fetch(self, work_id: str) -> str | None:
        """Full-text XML, cached on disk. A `<id>.missing` file in the cache directory is a MANUAL record that the paper
        has no downloadable full text (Europe PMC answers HTTP 500 for it); it is honoured for EPMC_MISSING_DAYS (30)
        days from the file's date, after which the paper is requested again. Records are never written automatically
        (user decision 2026-10-02)."""
        if work_id in self._xml:
            return self._xml[work_id]
        stem = re.sub(r"\W", "_", work_id)
        path = config.EPMC_CACHE_DIR / (stem + ".xml")
        marker = config.EPMC_CACHE_DIR / (stem + ".missing")
        if path.exists():
            x = path.read_text(encoding="utf-8")
        else:
            if marker.exists() and time.time() - marker.stat().st_mtime < config.EPMC_MISSING_DAYS * 86400:
                return None
            try:
                x = get(EPMC + f"{work_id}/fullTextXML", js=False, tries=FULLTEXT_TRIES, timeout=FULLTEXT_TIMEOUT)
            except Exception:  # noqa: BLE001
                return None
            with _write_lock:
                path.write_text(x, encoding="utf-8")
        self._xml[work_id] = x
        return x

    def full_text(self, work_id: str) -> str | None:
        if work_id not in self._text:
            x = self.fetch(work_id)
            if x is None:
                return None
            self._text[work_id] = plain(x)
        return self._text[work_id]

    # ---- screening ------------------------------------------------------------------------------------------
    def cites_query(self, kind: str, terms_or: list[str], since: str | None = None) -> str:
        src, pid = self.ids[kind]
        q = f"CITES:{pid}_{src} AND (" + " OR ".join(f'"{t}"' for t in terms_or) + ")"
        if since:
            q += f" AND FIRST_PDATE:[{since} TO 3000-12-31]"
        return q

    # ---- tools exposed to the workers (via MCP) ---------------------------------------------------------------
    def get_citation_sentences(self, work_id: str) -> dict:
        """Sentences of a citing paper in which the target is cited, matched through the paper's reference list."""
        x = self.fetch(work_id)
        if x is None:
            return {"error": "full text not available"}
        t = self.t
        rids = []
        frag = (t.get("title_fragment") or "").lower()[:35]
        for m in re.finditer(r"<ref\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</ref>", x, flags=re.S):
            body = plain(m.group(2)).lower()
            if (t.get("preprint_doi") and t["preprint_doi"].lower() in body) or (t.get("published_doi") and t["published_doi"].lower() in body) or \
                    (t.get("first_author") and frag and t["first_author"].lower() in body and frag in body):
                rids.append(m.group(1))
        marked = x
        for rid in rids:
            marked = re.sub(r"(<xref\b[^>]*\brid=\"[^\"]*\b" + re.escape(rid) + r"\b[^\"]*\"[^>]*>)", r"\1 [[CITES-TARGET]] ", marked)
        body = marked.split("<ref-list")[0]
        sents = sentences(plain(body))
        out = []
        for i, s in enumerate(sents):
            if "[[CITES-TARGET]]" in s or (not rids and t.get("first_author") and re.search(rf"\b{re.escape(t['first_author'])}\b", s)):
                ctx = " ".join(sents[max(0, i - 1):i + 2]).replace("[[CITES-TARGET]]", "").strip()
                out.append({"sentence": re.sub(r"\s+", " ", s.replace("[[CITES-TARGET]]", "")).strip()[:700],
                            "with_neighbours": re.sub(r"\s+", " ", ctx)[:1400]})
        return {"work_id": work_id, "reference_matched": bool(rids), "n_sentences": len(out), "sentences": out[:8]}

    def search_in_work(self, work_id: str, terms: str) -> dict:
        """Sentences of a citing paper containing ALL comma-separated terms."""
        t = self.full_text(work_id)
        if t is None:
            return {"error": "full text not available"}
        pats = [re.escape(x.strip()) for x in terms.split(",") if x.strip()]
        out = [s[:700] for s in sentences(t) if all(re.search(p, s, re.I) for p in pats)]
        return {"work_id": work_id, "terms": terms, "n_sentences": len(out), "sentences": out[:6]}

    def quote_found(self, work_id: str, quote: str) -> bool:
        from ..doctools import quote_in_text
        t = self.full_text(work_id)
        return bool(t and quote and quote_in_text(quote, t))
