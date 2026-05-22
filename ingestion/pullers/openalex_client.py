from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..common.doi import normalize_doi
from ..common.puller import PullerBase, PullerResult


class OpenAlexClient(PullerBase):
    BASE_URL = "https://api.openalex.org"

    def __init__(self, user_agent: str = "ClaimDrift/0.1 (+https://github.com/yourorg/claimdrift)"):
        super().__init__("openalex", user_agent=user_agent)

    def _work_url_for_doi(self, doi: str) -> str:
        return f"{self.BASE_URL}/works/{quote(f'https://doi.org/{doi}', safe='')}"

    def _list_works_url(self) -> str:
        return f"{self.BASE_URL}/works"

    def get_work_by_doi(self, doi: str) -> Dict[str, Any]:
        normalized = normalize_doi(doi)
        if not normalized:
            raise ValueError("OpenAlexClient requires a DOI.")
        return self._get_json(self._work_url_for_doi(normalized))

    def _author_rows(self, work: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
        rows = []
        for authorship in work.get("authorships", []):
            author = authorship.get("author") or {}
            rows.append(
                {
                    "name": author.get("display_name"),
                    "orcid": author.get("orcid"),
                    "email": None,
                }
            )
        return [row for row in rows if row["name"]]

    def _normalize_citing_work(self, work: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "citing_paper_doi": normalize_doi(work.get("doi")),
            "citing_paper_title": work.get("title") or work.get("display_name"),
            "citing_paper_authors": self._author_rows(work),
            "citation_context": None,
            "openalex_work_id": work.get("id"),
            "publication_year": work.get("publication_year"),
            "cited_by_count": work.get("cited_by_count"),
        }

    def find_citing_works(self, doi: str, limit: Optional[int] = None) -> Dict[str, Any]:
        source_work = self.get_work_by_doi(doi)
        source_id = source_work.get("id")
        if not source_id:
            raise RuntimeError(f"OpenAlex returned no work id for DOI {doi}.")

        per_page = min(limit or 25, 200)
        params = {
            "filter": f"cites:{source_id.rsplit('/', 1)[-1]}",
            "per-page": str(per_page),
            "select": "id,doi,title,display_name,authorships,publication_year,cited_by_count",
        }
        response = self._get_json(self._list_works_url(), params=params)
        results = response.get("results", [])
        if limit is not None:
            results = results[:limit]

        normalized = [self._normalize_citing_work(work) for work in results]
        with_doi = [row for row in normalized if row.get("citing_paper_doi")]

        return {
            "source_doi": normalize_doi(doi),
            "source_openalex_id": source_id,
            "total_found": response.get("meta", {}).get("count", len(results)),
            "processed": len(with_doi),
            "skipped_without_doi": len(normalized) - len(with_doi),
            "items": with_doi,
        }

    def run_pull(self, source: str, since: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        result = PullerResult(source=self.source)
        try:
            payload = self.find_citing_works(source, limit=limit)
            result.payload = payload["items"]
            result.fetched = payload["processed"]
            result.upserted = 0
            result.skipped = 0
            return {
                **result.to_dict(),
                "source_doi": payload["source_doi"],
                "source_openalex_id": payload["source_openalex_id"],
                "total_found": payload["total_found"],
                "processed": payload["processed"],
                "skipped_without_doi": payload["skipped_without_doi"],
            }
        except Exception as exc:
            result.errors.append(str(exc))
            return result.to_dict()
