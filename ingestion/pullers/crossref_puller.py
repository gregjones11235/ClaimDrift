from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..common.doi import normalize_doi
from ..common.puller import PullerBase, PullerResult


class CrossrefPuller(PullerBase):
    BASE_URL = "https://api.crossref.org/works"

    def __init__(self, user_agent: str = "ClaimDrift/0.1 (+https://github.com/yourorg/claimdrift)"):
        super().__init__("crossref", user_agent=user_agent)

    def _build_url(self, path: str) -> str:
        return f"{self.BASE_URL}/{path}"

    def _extract_published_doi(self, message: Dict[str, Any]) -> Optional[str]:
        relation = message.get("relation") or {}
        if isinstance(relation, dict):
            for predicate, entries in relation.items():
                if predicate.endswith("is-preprint-of"):
                    if isinstance(entries, dict):
                        entries = [entries]
                    for entry in entries:
                        doi = normalize_doi(entry.get("id"))
                        if doi:
                            return doi
        return None

    def _parse_work(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "doi": normalize_doi(message.get("DOI")),
            "title": " ".join(message.get("title", [])) if message.get("title") else None,
            "published_doi": self._extract_published_doi(message),
            "published_date": message.get("issued", {}).get("date-parts", [[None]])[0],
            "type": message.get("type"),
            "publisher": message.get("publisher"),
            "raw": message,
        }

    def run_pull(self, source: str, since: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        doi = normalize_doi(source)
        if not doi:
            raise ValueError("CrossrefPuller expects a DOI as the source argument.")

        result = PullerResult(source=self.source)
        try:
            response = self._get_json(self._build_url(doi))
            message = response.get("message")
            if not message:
                result.errors.append("Crossref returned no message payload.")
                return result.to_dict()

            record = self._parse_work(message)
            result.payload.append(record)
            result.fetched = 1
            if record.get("doi"):
                result.upserted = 1
        except Exception as exc:
            result.errors.append(str(exc))

        return result.to_dict()
