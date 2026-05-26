from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from ..common.puller import PullerBase, PullerResult
from ..common.doi import normalize_doi


class BioRxivPuller(PullerBase):
    BASE_URL = "https://api.biorxiv.org/details/biorxiv"

    def __init__(self, user_agent: str = "ClaimDrift/0.1 (+https://github.com/yourorg/claimdrift)"):
        super().__init__("biorxiv", user_agent=user_agent)

    def _build_url(self, since: str, until: str, cursor: int = 0) -> str:
        return f"{self.BASE_URL}/{since}/{until}/{cursor}"

    def _response_total(self, response: Dict[str, Any]) -> Optional[int]:
        messages = response.get("messages") or []
        if not messages:
            return None
        first = messages[0] if isinstance(messages, list) else messages
        if not isinstance(first, dict):
            return None
        for key in ("total", "count"):
            value = first.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    def _normalize_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        published = record.get("published")
        published_doi = None if not published or str(published).upper() == "NA" else normalize_doi(published)
        return {
            "doi": normalize_doi(record.get("doi")),
            "title": record.get("title"),
            "authors": record.get("authors"),
            "abstract": record.get("abstract"),
            "version": record.get("version"),
            "published_doi": published_doi,
            "posted_date": record.get("date"),
            "source": "biorxiv",
            "raw": record,
        }

    def run_pull(self, source: str, since: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        if source != "biorxiv":
            raise ValueError("BioRxivPuller expects source='biorxiv'.")

        end_date = datetime.utcnow().date().isoformat()
        start_date = since if since else end_date
        result = PullerResult(source=self.source)

        cursor = 0
        seen_ids: set[str] = set()
        try:
            while limit is None or result.fetched < limit:
                response = self._get_json(self._build_url(start_date, end_date, cursor))
                collection = response.get("collection", [])
                total = self._response_total(response)
                if not collection:
                    if cursor == 0:
                        result.errors.append("No records returned from BioRxiv API.")
                    break

                new_rows = 0
                remaining = None if limit is None else limit - result.fetched
                rows = collection if remaining is None else collection[:remaining]
                for row in rows:
                    normalized = self._normalize_record(row)
                    doc_key = f"{normalized.get('doi')}::{normalized.get('version')}"
                    if normalized["doi"] and doc_key not in seen_ids:
                        seen_ids.add(doc_key)
                        result.payload.append(normalized)
                        result.fetched += 1
                        result.upserted += 1
                        new_rows += 1
                    else:
                        result.skipped += 1

                cursor += len(collection)
                if total is not None and cursor >= total:
                    break
                if new_rows == 0 or (remaining is not None and result.fetched >= limit):
                    break
        except Exception as exc:
            result.errors.append(str(exc))

        return result.to_dict()
