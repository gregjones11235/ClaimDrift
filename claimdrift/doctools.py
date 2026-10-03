"""Paper full-text tools (served by the MCP tool service) and the verbatim quote check used by 事后校验.

The quote check is deliberately mechanical (no LLM): a citing researcher must be able to audit every piece of evidence
by string matching. Normalisation = lowercase, every run of non-alphanumerics collapsed to one space; quotes shorter than
15 normalised characters never verify (too easy to match by accident).
"""
from __future__ import annotations

import re

from .corpus import Doc

MAX_SECTION_CHARS = 6000
MAX_SNIPPETS = 8
SNIPPET_HALF = 220
MIN_QUOTE_CHARS = 15


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def squash(s: str) -> str:
    """Alphanumerics only. Tag stripping splits tokens the reader sees joined ("days<sup>13</sup>" -> "days 13" vs a
    copied "days13"), so a quote that fails the spaced comparison is tried once more without any separators."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def quote_in_text(quote: str, text: str) -> bool:
    q = norm(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return False
    return q in norm(text) or squash(quote) in squash(text)


class PaperTools:
    def __init__(self, docs: dict[str, Doc]):
        self.docs = docs
        self._norm_cache: dict[str, str] = {}

    def _doc(self, doc_id: str) -> Doc:
        if doc_id not in self.docs:
            raise KeyError(f"unknown doc_id {doc_id!r}; known: {list(self.docs)}")
        return self.docs[doc_id]

    def list_documents(self) -> dict:
        return {k: {"title": d.title, "n_sections": len(d.sections), "n_tables": len(d.tables), "n_chars": d.n_chars(),
                    "text_source": d.text_source} for k, d in self.docs.items()}

    def list_sections(self, doc_id: str) -> dict:
        d = self._doc(doc_id)
        return {"doc_id": doc_id, "sections": [{"title": s.title, "n_chars": len(s.text)} for s in d.sections],
                "tables": [{"index": i, "label": lbl, "n_chars": len(t)} for i, (lbl, t) in enumerate(d.tables)]}

    def read_section(self, doc_id: str, title: str) -> dict:
        d = self._doc(doc_id)
        tl = title.lower().strip()
        cands = [s for s in d.sections if s.title.lower() == tl] or \
                [s for s in d.sections if tl in s.title.lower()] or \
                [s for s in d.sections if any(w in s.title.lower() for w in tl.split() if len(w) > 3)]
        if not cands:
            return {"error": f"no section matching {title!r}; available: {[s.title for s in d.sections]}"}
        s = cands[0]
        text = s.text if len(s.text) <= MAX_SECTION_CHARS else s.text[:MAX_SECTION_CHARS] + " …[truncated]"
        return {"doc_id": doc_id, "section": s.title, "text": text}

    def search_text(self, doc_id: str, query: str) -> dict:
        """Keyword / number search with section + offset; total_hits == 0 is how ABSENCE is shown."""
        d = self._doc(doc_id)
        terms = [t for t in re.split(r"[\s,;]+", query.strip()) if t]
        if not terms:
            return {"error": "empty query"}
        pat = re.compile("|".join(re.escape(t) for t in terms), re.I)
        hits, total = [], 0
        for title, text in d.units():
            for m in pat.finditer(text):
                total += 1
                if len(hits) < MAX_SNIPPETS:
                    a, b = max(0, m.start() - SNIPPET_HALF), min(len(text), m.end() + SNIPPET_HALF)
                    hits.append({"section": title, "offset": m.start(), "match": m.group(0), "snippet": text[a:b]})
        return {"doc_id": doc_id, "query": query, "total_hits": total, "snippets": hits}

    def get_table(self, doc_id: str, index: int) -> dict:
        d = self._doc(doc_id)
        try:
            lbl, t = d.tables[int(index)]
        except (IndexError, ValueError):
            return {"error": f"table index out of range; {len(d.tables)} tables"}
        return {"doc_id": doc_id, "index": int(index), "label": lbl,
                "text": t if len(t) <= MAX_SECTION_CHARS else t[:MAX_SECTION_CHARS] + " …[truncated]"}

    # ---- 事后校验 -------------------------------------------------------------------------------------------
    def quote_found(self, doc_id: str, quote: str) -> bool:
        if doc_id not in self.docs or not quote:
            return False
        if doc_id not in self._norm_cache:
            full = self.docs[doc_id].full_text()
            self._norm_cache[doc_id] = (norm(full), squash(full))
        spaced, squashed = self._norm_cache[doc_id]
        q = norm(quote)
        return len(q) >= MIN_QUOTE_CHARS and (q in spaced or squash(quote) in squashed)

    def locate(self, doc_id: str, quote: str) -> tuple[str | None, int | None]:
        """(section title, offset in the normalised section text) of a verified quote."""
        d = self.docs.get(doc_id)
        q = norm(quote)
        if d is None or len(q) < MIN_QUOTE_CHARS:
            return None, None
        for title, text in d.units():
            i = norm(text).find(q)
            if i >= 0:
                return title, i
        return None, None

    def verify(self, doc_id: str, quote: str) -> dict:
        ok = self.quote_found(doc_id, quote)
        section, offset = self.locate(doc_id, quote) if ok else (None, None)
        d = self.docs.get(doc_id)
        return {"doc_id": doc_id, "verified": ok, "section": section, "offset": offset,
                "text_source": d.text_source if d else None}
