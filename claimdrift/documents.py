"""Resolve a (preprint, published) pair to full-text documents.

Comparison text is ALWAYS preprint v1 vs the published paper (§1): readers cite the version that existed when they read
it, and the bioRxiv `details` API returns the LATEST abstract for every version, so each version's text must come from
that version's own JATS (`text_source = "jats"`).

Two sources, same on-disk layout (preprint_v1.xml, ..., published_<id>.xml):
  case bank  data/cases/<slug>/              hand-verified pairs (validation scope, §0)
  docstore   data/docstore/<paper_id>/       pairs fetched on demand: v1 JATS from bioRxiv/medRxiv, published JATS from
                                             Europe PMC (open-access full text only)
`paper_id` is the directory name; the MCP server resolves it the same way.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .corpus import Doc, est_tokens, load_dir

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/"


@dataclass
class Pair:
    paper_id: str
    paper_dir: Path
    docs: dict[str, Doc]
    preprint_doi: str
    published_doi: str
    title: str = ""
    first_author: str = ""
    versions: list[str] = field(default_factory=list)
    withdrawn_versions: list[str] = field(default_factory=list)
    source: str = "case_bank"  # case_bank | docstore

    pre_id: str = "preprint_v1"

    @property
    def preprint(self) -> Doc:
        return self.docs[self.pre_id]

    @property
    def published(self) -> Doc:
        return self.docs["published"]

    def est_tokens(self) -> int:
        return est_tokens(self.preprint, self.published)


def paper_dir(paper_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", paper_id or ""):
        raise ValueError(f"invalid paper_id {paper_id!r}")
    for base in (config.CASES_DIR, config.DOCSTORE_DIR):
        p = base / paper_id
        if p.is_dir() and list(p.glob("preprint_v1.xml")):
            return p
    raise FileNotFoundError(f"no documents for paper_id {paper_id!r} (looked in case bank and docstore)")


def paper_id_for_doi(preprint_doi: str) -> str:
    return re.sub(r"[^A-Za-z0-9.\-]+", "_", preprint_doi.strip().lower())


def _meta_from_details(details: list[dict]) -> dict:
    first = details[0] if details else {}
    authors = str(first.get("authors") or "")
    return {"preprint_doi": str(first.get("doi") or "").lower(),
            "published_doi": str((details[-1] if details else {}).get("published") or "").lower(),
            "title": first.get("title") or "",
            "first_author": authors.split(",")[0].split(";")[0].strip(),
            "versions": [f"v{h.get('version')}" for h in details],
            "withdrawn_versions": [f"v{h.get('version')}" for h in details if str(h.get("type", "")).lower() == "withdrawn"]}


def load_pair(paper_id: str) -> Pair:
    d = paper_dir(paper_id)
    docs = load_dir(d)
    if "preprint_v1" not in docs or "published" not in docs:
        raise FileNotFoundError(f"{paper_id}: need preprint_v1.xml and published_*.xml, have {list(docs)}")
    if (d / "meta.json").exists():
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        source = "docstore"
    else:
        meta = _meta_from_details(json.loads((d / "preprint_details.json").read_text(encoding="utf-8")).get("collection") or [])
        source = "case_bank"
    return Pair(paper_id=paper_id, paper_dir=d, docs=docs, source=source,
                **{k: meta.get(k) or ([] if k in ("versions", "withdrawn_versions") else "") for k in
                   ("preprint_doi", "published_doi", "title", "first_author", "versions", "withdrawn_versions")})


# ---------------------------------------------------------------- on-demand fetch (layer ① for pairs outside the case bank)
def _versions_module():
    if str(config.ROOT) not in sys.path:
        sys.path.insert(0, str(config.ROOT))
    from apps.dispatcher import versions  # stdlib-only module, shared with the dispatcher
    return versions


def _epmc_get(url: str, js: bool = True):
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "claimdrift"}), timeout=60) as r:
        b = r.read()
    return json.loads(b) if js else b


def fetch_published_jats(published_doi: str) -> tuple[str | None, bytes | None]:
    """(PMCID, JATS) of the published paper from Europe PMC; (None, None) when no open full text exists."""
    q = urllib.parse.urlencode({"query": f'DOI:"{published_doi}"', "format": "json", "resultType": "lite"})
    res = _epmc_get(EPMC + "search?" + q).get("resultList", {}).get("result", [])
    pmcid = next((h.get("pmcid") for h in res if h.get("pmcid")), None)
    if not pmcid:
        return None, None
    try:
        return pmcid, _epmc_get(EPMC + f"{pmcid}/fullTextXML", js=False)
    except Exception:  # noqa: BLE001
        return pmcid, None


def fetch_pair(preprint_doi: str, published_doi: str | None = None, server_hint: str | None = None) -> Pair:
    """Fetch v1 + published JATS into the docstore (idempotent) and load the pair. Raises when either full text is missing:
    the caller must NOT fall back to a later version or to the API abstract."""
    pid = paper_id_for_doi(preprint_doi)
    d = config.DOCSTORE_DIR / pid
    if (d / "preprint_v1.xml").exists() and list(d.glob("published_*.xml")):
        return load_pair(pid)
    V = _versions_module()
    server, history = V._fetch_history_sync(preprint_doi, server_hint)
    first = V.first_version_entry(history)
    if first is None:
        raise LookupError(f"v1 unavailable for {preprint_doi} (versions {[h.get('version') for h in history]})")
    v1 = V.fetch_jats_xml_sync(first.get("jatsxml") or "")
    if not v1:
        raise LookupError(f"v1 JATS unavailable for {preprint_doi}")
    pub_doi = (published_doi or _meta_from_details(history)["published_doi"] or "").lower()
    if not pub_doi:
        raise LookupError(f"{preprint_doi}: no published DOI")
    pmcid, pub = fetch_published_jats(pub_doi)
    if not pub:
        raise LookupError(f"published full text not open in Europe PMC for {pub_doi}")
    d.mkdir(parents=True, exist_ok=True)
    (d / "preprint_v1.xml").write_bytes(v1)
    (d / f"published_{pmcid}.xml").write_bytes(pub)
    meta = _meta_from_details(history) | {"published_doi": pub_doi, "server": server, "published_pmcid": pmcid}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return load_pair(pid)


def case_bank_ids() -> list[str]:
    gold = json.loads((config.EXPERIMENTS_DIR / "gold.json").read_text(encoding="utf-8"))["cases"]
    return list(gold)
