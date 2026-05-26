from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, Optional

from ..common.doi import normalize_doi
from ..common.puller import PullerBase, PullerResult


class ArxivPuller(PullerBase):
    BASE_URL = "https://export.arxiv.org/oai2"
    REQUEST_INTERVAL_SECONDS = 3.0
    NS = {
        "oai": "http://www.openarchives.org/OAI/2.0/",
        "arxiv": "http://arxiv.org/OAI/arXiv/",
    }

    def __init__(self, user_agent: str = "ClaimDrift/0.1 (+https://github.com/yourorg/claimdrift)"):
        super().__init__("arxiv", user_agent=user_agent)

    def _get_xml(self, params: Dict[str, str], retry: int = 3, backoff: float = 3.0) -> ET.Element:
        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
        last_error = None
        for attempt in range(1, retry + 1):
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return ET.fromstring(response.read())
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                last_error = f"HTTP {exc.code}: {exc.reason} ({body[:200]})"
            except urllib.error.URLError as exc:
                last_error = str(exc)
            except ET.ParseError as exc:
                last_error = f"XML parse error: {exc}"

            if attempt < retry:
                time.sleep(backoff * attempt)

        raise RuntimeError(f"Failed to fetch XML from {url}: {last_error}")

    def _text(self, node: Optional[ET.Element], path: str) -> Optional[str]:
        if node is None:
            return None
        found = node.find(path, self.NS)
        if found is None or found.text is None:
            return None
        return " ".join(found.text.split()) or None

    def _authors(self, metadata: ET.Element) -> list[Dict[str, Optional[str]]]:
        authors = []
        for author in metadata.findall("arxiv:authors/arxiv:author", self.NS):
            keyname = self._text(author, "arxiv:keyname")
            forenames = self._text(author, "arxiv:forenames")
            suffix = self._text(author, "arxiv:suffix")
            name = " ".join(part for part in (forenames, keyname, suffix) if part)
            affiliation = self._text(author, "arxiv:affiliation")
            if name:
                authors.append({"name": name, "orcid": None, "affiliation": affiliation})
        return authors

    def _arxiv_doi(self, arxiv_id: Optional[str]) -> Optional[str]:
        if not arxiv_id:
            return None
        return normalize_doi(f"10.48550/arXiv.{arxiv_id}")

    def _published_doi(self, metadata: ET.Element) -> Optional[str]:
        doi = normalize_doi(self._text(metadata, "arxiv:doi"))
        if not doi or doi.startswith("10.48550/arxiv."):
            return None
        return doi

    def _normalize_record(self, record: ET.Element) -> Optional[Dict[str, Any]]:
        metadata = record.find("oai:metadata/arxiv:arXiv", self.NS)
        if metadata is None:
            return None

        arxiv_id = self._text(metadata, "arxiv:id")
        doi = self._arxiv_doi(arxiv_id)
        if not doi:
            return None

        published_doi = self._published_doi(metadata)
        return {
            "doi": doi,
            "title": self._text(metadata, "arxiv:title"),
            "authors": self._authors(metadata),
            "abstract": self._text(metadata, "arxiv:abstract"),
            "version": "v1",
            "published_doi": published_doi,
            "posted_date": self._text(metadata, "arxiv:created"),
            "source": "arxiv",
            "raw": {
                "arxiv_id": arxiv_id,
                "categories": self._text(metadata, "arxiv:categories"),
                "journal_ref": self._text(metadata, "arxiv:journal-ref"),
                "published_doi": published_doi,
            },
        }

    def _list_records_params(
        self,
        *,
        since: str,
        until: str,
        arxiv_set: Optional[str],
        resumption_token: Optional[str] = None,
    ) -> Dict[str, str]:
        if resumption_token:
            return {"verb": "ListRecords", "resumptionToken": resumption_token}
        params = {
            "verb": "ListRecords",
            "metadataPrefix": "arXiv",
            "from": since,
            "until": until,
        }
        if arxiv_set:
            params["set"] = arxiv_set
        return params

    def run_pull(
        self,
        source: str,
        since: Optional[str] = None,
        limit: Optional[int] = None,
        arxiv_set: Optional[str] = "q-bio",
    ) -> Dict[str, Any]:
        if source != "arxiv":
            raise ValueError("ArxivPuller expects source='arxiv'.")

        end_date = datetime.utcnow().date().isoformat()
        start_date = since if since else end_date
        result = PullerResult(source=self.source)
        resumption_token = None
        request_count = 0

        try:
            while limit is None or result.fetched < limit:
                if request_count:
                    time.sleep(self.REQUEST_INTERVAL_SECONDS)
                response = self._get_xml(
                    self._list_records_params(
                        since=start_date,
                        until=end_date,
                        arxiv_set=arxiv_set,
                        resumption_token=resumption_token,
                    )
                )
                request_count += 1

                errors = response.findall(".//oai:error", self.NS)
                if errors:
                    result.errors.extend(
                        " ".join(error.itertext()).strip()
                        for error in errors
                        if " ".join(error.itertext()).strip()
                    )
                    break

                records = response.findall(".//oai:ListRecords/oai:record", self.NS)
                if not records:
                    if request_count == 1:
                        result.errors.append("No records returned from arXiv OAI-PMH.")
                    break

                remaining = None if limit is None else limit - result.fetched
                for record in records if remaining is None else records[:remaining]:
                    normalized = self._normalize_record(record)
                    if normalized:
                        result.payload.append(normalized)
                        result.fetched += 1
                        result.upserted += 1
                    else:
                        result.skipped += 1

                if limit is not None and result.fetched >= limit:
                    break

                token_node = response.find(".//oai:resumptionToken", self.NS)
                resumption_token = token_node.text.strip() if token_node is not None and token_node.text else None
                if not resumption_token:
                    break
        except Exception as exc:
            result.errors.append(str(exc))

        return result.to_dict()
