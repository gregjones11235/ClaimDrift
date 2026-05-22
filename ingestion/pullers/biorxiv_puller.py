from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..common.puller import PullerBase, PullerResult
from ..common.doi import normalize_doi


class BioRxivPuller(PullerBase):
    BASE_URL = "https://api.biorxiv.org/details/biorxiv"

    def __init__(self, user_agent: str = "ClaimDrift/0.1 (+https://github.com/yourorg/claimdrift)"):
        super().__init__("biorxiv", user_agent=user_agent)

    def _build_url(self, since: str, until: str) -> str:
        return f"{self.BASE_URL}/{since}/{until}"

    def _normalize_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "doi": normalize_doi(record.get("doi")),
            "title": record.get("title"),
            "authors": record.get("authors"),
            "abstract": record.get("abstract"),
            "version": record.get("version"),
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

        try:
            response = self._get_json(self._build_url(start_date, end_date))
            collection = response.get("collection", [])
            for row in collection[:limit] if limit is not None else collection:
                normalized = self._normalize_record(row)
                if normalized["doi"]:
                    result.payload.append(normalized)
                    result.fetched += 1
                    result.upserted += 1
                else:
                    result.skipped += 1
            if not collection:
                result.errors.append("No records returned from BioRxiv API.")
        except Exception as exc:
            result.errors.append(str(exc))

        return result.to_dict()
