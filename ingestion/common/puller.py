from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_USER_AGENT = "ClaimDrift/0.1 (+https://github.com/yourorg/claimdrift)"


@dataclass
class PullerResult:
    source: str
    fetched: int = 0
    upserted: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    payload: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "fetched": self.fetched,
            "upserted": self.upserted,
            "skipped": self.skipped,
            "errors": self.errors,
            "payload": self.payload,
        }


class PullerBase:
    def __init__(self, source: str, user_agent: str = DEFAULT_USER_AGENT):
        self.source = source
        self.user_agent = user_agent

    def _get_json(self, url: str, params: Optional[Dict[str, str]] = None, retry: int = 3, backoff: float = 1.0) -> Any:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        last_error = None

        for attempt in range(1, retry + 1):
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    content = response.read().decode("utf-8")
                    return json.loads(content)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                last_error = f"HTTP {exc.code}: {exc.reason} ({body[:200]})"
            except urllib.error.URLError as exc:
                last_error = str(exc)
            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error: {exc}"

            if attempt < retry:
                time.sleep(backoff * attempt)

        raise RuntimeError(f"Failed to fetch JSON from {url}: {last_error}")

    def run_pull(self, source: str, since: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        raise NotImplementedError("Puller implementations must override run_pull.")
