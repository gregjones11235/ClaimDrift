"""JSON form of documents and pairs. Agent Engine has no bucket mount, so the caller (Cloud Run) parses the two JATS
files and sends the documents with the request (部署方案 A3); only the two documents compared travel."""
from __future__ import annotations

from pathlib import Path

from .corpus import Doc, Section
from .documents import Pair

PAIR_FIELDS = ("paper_id", "preprint_doi", "published_doi", "title", "first_author", "versions", "withdrawn_versions",
               "source", "pre_id")


def doc_to_dict(d: Doc) -> dict:
    return {"id": d.id, "title": d.title, "abstract": d.abstract, "sections": [[s.title, s.text] for s in d.sections],
            "tables": [list(t) for t in d.tables], "text_source": d.text_source}


def doc_from_dict(x: dict) -> Doc:
    return Doc(x["id"], x["title"], x["abstract"], [Section(t, s) for t, s in x["sections"]],
               [(lbl, t) for lbl, t in x["tables"]], x.get("text_source", "jats"))


def pair_to_dict(p: Pair) -> dict:
    return {k: getattr(p, k) for k in PAIR_FIELDS} | {"docs": {k: doc_to_dict(p.docs[k]) for k in (p.pre_id, "published")}}


def pair_from_dict(x: dict) -> Pair:
    return Pair(paper_dir=Path(""), docs={k: doc_from_dict(v) for k, v in x["docs"].items()}, **{k: x[k] for k in PAIR_FIELDS})
