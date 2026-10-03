"""Pick the preprint version that drift analysis compares against the published paper.

Decision (2026-10-02, see agent项目升级改造.md "任务 6"): compare the FIRST posted version (v1),
not the latest one. Readers cite whatever version existed when they read it; in the case bank 11 of
14 multi-version drifts happened between preprint versions and were invisible to a
latest-version-vs-published comparison, and published papers were found still citing the v1-only
numbers years later.

Ingestion stores one `preprints` row per version (`{doi}::{version}`) but pulls by posting-date
window, so v1 may be absent. Worse (verified 2026-10-02 on 4/4 case-bank papers): the bioRxiv/medRxiv
`details` API returns the SAME abstract for every version -- the LATEST one -- so every version row that
ingestion wrote from that API carries the latest text. The only reliable per-version text is that
version's own JATS XML (`jatsxml` link in the API response). Therefore v1 text is always taken from the
v1 JATS and stored with `text_source = "jats"`; an ES v1 row without that marker is refetched.
The same API response carries each version's `type`; a version of type "withdrawn" is reported so the
drift event can record it (a withdrawal notice must never be used as the comparison text).

Dependency-free on purpose (stdlib + the ES client passed in) so it can be tested without the
dispatcher's Vertex/Gmail stack.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("dispatcher.versions")

BIORXIV_API = "https://api.biorxiv.org/details/{server}/{doi}"
SERVERS = ("biorxiv", "medrxiv")


def _clean(text: Any) -> str | None:
    if text is None:
        return None
    t = re.sub(r"<[^>]+>", "", html.unescape(str(text)))
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def _vnum(version: Any) -> int:
    m = re.search(r"\d+", str(version or ""))
    return int(m.group()) if m else 10**6


def _fetch_history_sync(doi: str, server_hint: str | None) -> tuple[str | None, list[dict]]:
    servers = [server_hint] + [s for s in SERVERS if s != server_hint] if server_hint in SERVERS else list(SERVERS)
    for server in servers:
        req = urllib.request.Request(BIORXIV_API.format(server=server, doi=doi),
                                     headers={"User-Agent": "ClaimDrift dispatcher"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                coll = json.load(r).get("collection") or []
        except Exception:  # noqa: BLE001
            log.exception("version history fetch failed server=%s doi=%s", server, doi)
            continue
        if coll:
            return server, coll
    return None, []


async def fetch_version_history(doi: str, server_hint: str | None = None) -> tuple[str | None, list[dict]]:
    return await asyncio.to_thread(_fetch_history_sync, doi, server_hint)


def fetch_jats_xml_sync(url: str) -> bytes | None:
    """Raw JATS XML of one version (the full text, not just the abstract). Retries on HTTP 429 (medRxiv rate-limits bursts)."""
    import time
    if not url:
        return None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ClaimDrift dispatcher)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 4:
                log.exception("jats fetch failed %s", url)
                return None
            time.sleep(5 * (attempt + 1))
        except Exception:  # noqa: BLE001
            log.exception("jats fetch failed %s", url)
            return None
    return None


def first_version_entry(history: list[dict]) -> dict | None:
    """The v1 entry of a version history, or None when v1 is missing or is itself a withdrawal notice."""
    first = min(history, key=lambda h: _vnum(h.get("version")), default=None)
    if first is None or _vnum(first.get("version")) != 1 or str(first.get("type", "")).lower() == "withdrawn":
        return None
    return first


def _jats_text_sync(url: str) -> tuple[str | None, str | None]:
    """(title, abstract) from a version's own JATS XML."""
    import xml.etree.ElementTree as ET
    raw = fetch_jats_xml_sync(url)
    if raw is None:
        return None, None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        log.exception("jats parse failed %s", url)
        return None, None
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    join = lambda el: re.sub(r"\s+", " ", " ".join(t.strip() for t in el.itertext() if t.strip())).strip() if el is not None else None  # noqa: E731
    title = join(root.find(".//article-title"))
    abstract = join(root.find(".//abstract"))
    if abstract and abstract.lower().startswith("abstract "):
        abstract = abstract[9:]
    return title, abstract


def withdrawn_versions(history: list[dict]) -> list[str]:
    return [f"v{h.get('version')}" for h in history if str(h.get("type", "")).lower() == "withdrawn"]


def row_from_history(entry: dict, server: str) -> dict[str, Any]:
    """Same shape as ingestion.common.records.preprint_record_from_puller (kept in sync by hand)."""
    published = entry.get("published")
    published = None if not published or str(published).upper() == "NA" else str(published).lower()
    return {
        "doi": str(entry.get("doi", "")).lower(),
        "source": server,
        "version": f"v{entry.get('version')}",
        "is_final_preprint": False,  # never the dispatch trigger; only the comparison text
        "published_doi": published,
        "title": _clean(entry.get("title")),
        "abstract": _clean(entry.get("abstract")),
        "conclusion": None,
        "authors": [{"name": a.strip(), "orcid": None, "affiliation": None}
                    for a in str(entry.get("authors") or "").split(";") if a.strip()],
        "posted_date": entry.get("date"),
        "text_source": "api",  # replaced by "jats" once the version's own text is fetched
    }


async def fetch_first_version(es, doi: str, *, index: str = "preprints") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return (v1 row with v1's OWN text, info). info = {"source_of_v1": "es"|"jats"|None, "versions", "withdrawn"}."""
    resp = await es.search(index=index, size=50, query={"bool": {
        "filter": [{"term": {"doi": doi}}], "must_not": [{"term": {"version": "published"}}]}})
    rows = [h["_source"] for h in resp["hits"]["hits"]]
    rows.sort(key=lambda r: _vnum(r.get("version")))
    server_hint = rows[0].get("source") if rows else None
    server, history = await fetch_version_history(doi, server_hint)
    info: dict[str, Any] = {"versions": [f"v{h.get('version')}" for h in history] or [r.get("version") for r in rows],
                            "withdrawn": withdrawn_versions(history), "source_of_v1": None}
    if rows and _vnum(rows[0].get("version")) == 1 and rows[0].get("text_source") == "jats":
        info["source_of_v1"] = "es"
        return rows[0], info
    first = min(history, key=lambda h: _vnum(h.get("version")), default=None)
    if first is None or _vnum(first.get("version")) != 1:
        log.warning("v1 unavailable for doi=%s (es versions=%s, api versions=%s)", doi,
                    [r.get("version") for r in rows], info["versions"])
        return None, info  # caller must NOT fall back to a later version silently
    if str(first.get("type", "")).lower() == "withdrawn":
        log.warning("v1 of doi=%s is itself a withdrawal notice", doi)
        return None, info
    title, abstract = await asyncio.to_thread(_jats_text_sync, first.get("jatsxml") or "")
    if not abstract:
        log.warning("v1 JATS text unavailable for doi=%s", doi)
        return None, info  # the API abstract is the LATEST version's text; never use it as v1
    row = row_from_history(first, server)
    row.update({"title": title or row["title"], "abstract": abstract, "text_source": "jats"})
    await es.index(index=index, id=f"{row['doi']}::v1", document=row, refresh="wait_for")
    info["source_of_v1"] = "jats"
    return row, info
