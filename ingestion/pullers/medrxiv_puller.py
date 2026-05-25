from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from ..common.puller import PullerBase, PullerResult
from ..common.doi import normalize_doi


class MedRxivPuller(PullerBase):
    BASE_URL = "https://api.biorxiv.org/details/medrxiv"

    def __init__(self, user_agent: str = "ClaimDrift/0.1 (+https://github.com/yourorg/claimdrift)"):
        super().__init__("medrxiv", user_agent=user_agent)

    def _build_url(self, since: str, until: str, cursor: int = 0) -> str:
        return f"{self.BASE_URL}/{since}/{until}/{cursor}"

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
            "source": "medrxiv",
            "raw": record,
        }

    def run_pull(self, source: str, since: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        if source != "medrxiv":
            raise ValueError("MedRxivPuller expects source='medrxiv'.")

        end_date = datetime.utcnow().date().isoformat()
        start_date = since if since else end_date
        result = PullerResult(source=self.source)

        cursor = 0
        page_size = 100
        try:
            while limit is None or result.fetched < limit:
                response = self._get_json(self._build_url(start_date, end_date, cursor))
                collection = response.get("collection", [])
                if not collection:
                    if cursor == 0:
                        result.errors.append("No records returned from MedRxiv API.")
                    break

                remaining = None if limit is None else limit - result.fetched
                rows = collection if remaining is None else collection[:remaining]
                for row in rows:
                    normalized = self._normalize_record(row)
                    if normalized["doi"]:
                        result.payload.append(normalized)
                        result.fetched += 1
                        result.upserted += 1
                    else:
                        result.skipped += 1

                if len(collection) < page_size or len(rows) < len(collection):
                    break
                cursor += len(collection)
        except Exception as exc:
            result.errors.append(str(exc))

        return result.to_dict()
