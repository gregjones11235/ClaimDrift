from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple


class ElasticsearchConfigError(RuntimeError):
    pass


class ElasticsearchHttpClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.base_url = (
            base_url
            or os.getenv("ELASTIC_ENDPOINT")
            or os.getenv("ELASTICSEARCH_URL")
            or ""
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.getenv("ELASTIC_API_KEY")
            or os.getenv("ELASTICSEARCH_API_KEY")
        )
        self.username = username or os.getenv("ELASTICSEARCH_USERNAME")
        self.password = password or os.getenv("ELASTICSEARCH_PASSWORD")

        if not self.base_url:
            raise ElasticsearchConfigError("ELASTIC_ENDPOINT (or legacy ELASTICSEARCH_URL) is required for --apply.")
        if not self.api_key and not (self.username and self.password):
            raise ElasticsearchConfigError(
                "Set ELASTIC_API_KEY (or legacy ELASTICSEARCH_API_KEY), or ELASTICSEARCH_USERNAME/ELASTICSEARCH_PASSWORD for --apply."
            )

    def _headers(self, content_type: str = "application/json") -> Dict[str, str]:
        headers = {"Content-Type": content_type}
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"
        elif self.username and self.password:
            token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def request(self, method: str, path: str, body: Optional[Any] = None, content_type: str = "application/json") -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = None
        if body is not None:
            if content_type == "application/x-ndjson":
                data = str(body).encode("utf-8")
            else:
                data = json.dumps(body).encode("utf-8")

        request = urllib.request.Request(url, data=data, method=method, headers=self._headers(content_type))
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read().decode("utf-8")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Elasticsearch {method} {path} failed: HTTP {exc.code} {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Elasticsearch {method} {path} failed: {exc}") from exc

    def put_pipeline(self, pipeline_id: str, body: Dict[str, Any]) -> Any:
        return self.request("PUT", f"/_ingest/pipeline/{urllib.parse.quote(pipeline_id)}", body)

    def put_index(self, index_name: str, body: Dict[str, Any]) -> Any:
        return self.request("PUT", f"/{urllib.parse.quote(index_name)}", body)

    def bulk_index(self, index_name: str, rows: Iterable[Tuple[str, Dict[str, Any]]]) -> Dict[str, Any]:
        lines: List[str] = []
        count = 0
        for doc_id, row in rows:
            lines.append(json.dumps({"index": {"_index": index_name, "_id": doc_id}}))
            lines.append(json.dumps(row))
            count += 1

        if not lines:
            return {"errors": False, "items": [], "took": 0, "count": 0}

        body = "\n".join(lines) + "\n"
        response = self.request("POST", "/_bulk", body, content_type="application/x-ndjson")
        response["count"] = count
        return response
