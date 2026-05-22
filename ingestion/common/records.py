from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .doi import normalize_doi


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    if "T" in text:
        return text
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return text
    return parsed.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_version(value: Any) -> str:
    if value is None:
        return "v1"
    text = str(value).strip()
    if not text:
        return "v1"
    if text == "published" or text.startswith("v"):
        return text
    return f"v{text}"


def normalize_authors(value: Any) -> List[Dict[str, Optional[str]]]:
    if value is None:
        return []

    if isinstance(value, list):
        authors = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("full_name")
                authors.append(
                    {
                        "name": str(name).strip() if name else None,
                        "orcid": item.get("orcid"),
                        "affiliation": item.get("affiliation"),
                    }
                )
            elif item:
                authors.append({"name": str(item).strip(), "orcid": None, "affiliation": None})
        return [author for author in authors if author["name"]]

    names = [name.strip() for name in str(value).replace(" and ", ";").split(";")]
    return [
        {"name": name, "orcid": None, "affiliation": None}
        for name in names
        if name
    ]


def clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip() or None


def preprint_record_from_puller(row: Dict[str, Any], ingested_at: Optional[str] = None) -> Dict[str, Any]:
    return {
        "doi": normalize_doi(row.get("doi")),
        "source": row.get("source"),
        "version": normalize_version(row.get("version")),
        "is_final_preprint": False,
        "published_doi": normalize_doi(row.get("published_doi")),
        "title": clean_text(row.get("title")),
        "abstract": clean_text(row.get("abstract")),
        "conclusion": clean_text(row.get("conclusion")),
        "authors": normalize_authors(row.get("authors")),
        "posted_date": parse_date(row.get("posted_date")),
        "ingested_at": ingested_at or utc_now(),
    }


def crossref_record_from_puller(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "doi": normalize_doi(row.get("doi")),
        "published_doi": normalize_doi(row.get("published_doi")),
        "title": clean_text(row.get("title")),
        "published_date": row.get("published_date"),
        "type": row.get("type"),
        "publisher": row.get("publisher"),
    }
