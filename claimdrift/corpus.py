"""JATS -> structured documents. bioRxiv/medRxiv `.source.xml` and Europe PMC `fullTextXML` are both NLM JATS, so one
parser covers the preprint and the published side. A Doc exposes the abstract, sections (titles like
"Methods > Study Definitions") and tables (flattened rows). Ported from data/cases/experiments/corpus.py unchanged in
behaviour so quote verification and token estimates match the experiments.
"""
from __future__ import annotations

import glob
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


def _txt(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


@dataclass
class Section:
    title: str
    text: str


@dataclass
class Doc:
    id: str  # preprint_v1 | preprint_v2 | ... | published
    title: str
    abstract: str
    sections: list[Section] = field(default_factory=list)
    tables: list[tuple[str, str]] = field(default_factory=list)  # (label, text)
    text_source: str = "jats"

    def full_text(self) -> str:
        parts = [f"## Abstract\n{self.abstract}"]
        parts += [f"## {s.title}\n{s.text}" for s in self.sections]
        parts += [f"## Table: {lbl}\n{txt}" for lbl, txt in self.tables]
        return "\n\n".join(parts)

    def n_chars(self) -> int:
        return len(self.full_text())

    def units(self):
        """(title, text) for the abstract, every section and every table -- the search/verification universe."""
        yield "Abstract", self.abstract
        for s in self.sections:
            yield s.title, s.text
        for lbl, t in self.tables:
            yield f"Table: {lbl}", t

    def outline(self) -> list[str]:
        return [s.title for s in self.sections] + [f"Table: {lbl}" for lbl, _ in self.tables]


def _walk_secs(sec, path: list[str], out: list[Section]) -> None:
    title = _txt(sec.find("title")) or "(untitled)"
    here = path + [title]
    own = [t for child in sec if child.tag not in ("sec", "title", "table-wrap", "fig") for t in [_txt(child)] if t]
    if own:
        out.append(Section(" > ".join(here), "\n".join(own)))
    for child in sec.findall("sec"):
        _walk_secs(child, here, out)


def _table_text(tw) -> tuple[str, str]:
    label = _txt(tw.find("label")) or "table"
    cap = _txt(tw.find("caption"))
    rows = []
    for tr in tw.iter("tr"):
        cells = [_txt(c) for c in tr if c.tag in ("td", "th")]
        if any(cells):
            rows.append(" | ".join(cells))
    return label, (cap + "\n" if cap else "") + "\n".join(rows)


def parse_jats_root(root, doc_id: str) -> Doc:
    for el in root.iter():  # strip namespaces for simpler .find
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    title = _txt(root.find(".//article-title"))
    ab = root.find(".//abstract")
    # join with spaces: structured abstracts otherwise glue headings to text ("BackgroundSince...")
    abstract = re.sub(r"\s+", " ", " ".join(t.strip() for t in ab.itertext() if t.strip())) if ab is not None else ""
    doc = Doc(doc_id, title, abstract)
    body = root.find(".//body")
    if body is not None:
        for sec in body.findall("sec"):
            _walk_secs(sec, [], doc.sections)
        loose = [_txt(p) for p in body.findall("p") if _txt(p)]  # some PMC bodies put paragraphs directly under body
        if loose:
            doc.sections.insert(0, Section("Body", "\n".join(loose)))
    for tw in root.iter("table-wrap"):
        doc.tables.append(_table_text(tw))
    return doc


def parse_jats(path: str, doc_id: str) -> Doc:
    return parse_jats_root(ET.parse(path).getroot(), doc_id)


def parse_jats_string(xml: str | bytes, doc_id: str) -> Doc:
    return parse_jats_root(ET.fromstring(xml), doc_id)


def load_dir(paper_dir: str | os.PathLike) -> dict[str, Doc]:
    """Every preprint version plus the published paper of one paper directory (case bank or docstore layout)."""
    paper_dir = str(paper_dir)
    docs: dict[str, Doc] = {}
    for p in sorted(glob.glob(os.path.join(paper_dir, "preprint_v*.xml")), key=lambda p: int(re.search(r"preprint_v(\d+)", p).group(1))):
        v = re.search(r"preprint_v(\d+)", p).group(1)
        docs[f"preprint_v{v}"] = parse_jats(p, f"preprint_v{v}")
    for p in glob.glob(os.path.join(paper_dir, "published_*.xml")):
        docs["published"] = parse_jats(p, "published")
    return docs


def est_tokens(*docs: Doc) -> int:
    """Same estimate the routing experiments used: characters / 4."""
    return sum(d.n_chars() for d in docs) // 4
