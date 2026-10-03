"""Minimal Elasticsearch client (stdlib only) for the local node."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config


class ESError(RuntimeError):
    def __init__(self, status: int, body: str, method: str, path: str):
        super().__init__(f"{method} {path}: {status} {body[:600]}")
        self.status = status
        self.body = body


def request(method: str, path: str, body=None, ndjson: str | None = None, timeout: int = 120):
    headers = {"Content-Type": "application/x-ndjson" if ndjson is not None else "application/json"}
    if config.ES_API_KEY:
        headers["Authorization"] = f"ApiKey {config.ES_API_KEY}"
    data = ndjson.encode() if ndjson is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"{config.ES_ENDPOINT}/{path.lstrip('/')}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise ESError(e.code, e.read().decode("utf-8", "ignore"), method, path) from e


def doc_path(index: str, doc_id: str) -> str:
    return f"{index}/_doc/{urllib.parse.quote(doc_id, safe='')}"


def put(index: str, doc_id: str, doc: dict, refresh: str = "wait_for", **params) -> dict:
    q = urllib.parse.urlencode({"refresh": refresh, **params})
    return request("PUT", f"{doc_path(index, doc_id)}?{q}", doc)


def get(index: str, doc_id: str) -> dict | None:
    try:
        r = request("GET", doc_path(index, doc_id))
    except ESError as e:
        if e.status == 404:
            return None
        raise
    return r if r.get("found") else None


def source(index: str, doc_id: str) -> dict | None:
    r = get(index, doc_id)
    return r["_source"] if r else None


def update(index: str, doc_id: str, partial: dict, refresh: str = "wait_for") -> dict:
    return request("POST", f"{index}/_update/{urllib.parse.quote(doc_id, safe='')}?refresh={refresh}", {"doc": partial})


def search(index: str, body: dict) -> dict:
    return request("POST", f"{index}/_search", body)


def hits(index: str, query: dict | None = None, size: int = 1000, sort=None, source_fields=None) -> list[dict]:
    body: dict = {"size": size, "query": query or {"match_all": {}}}
    if sort:
        body["sort"] = sort
    if source_fields is not None:
        body["_source"] = source_fields
    return [h["_source"] | {"_id": h["_id"]} for h in search(index, body)["hits"]["hits"]]


def bulk_index(index: str, rows: list[tuple[str, dict]], refresh: str = "wait_for") -> dict:
    if not rows:
        return {"errors": False, "items": []}
    nd = "".join(json.dumps({"index": {"_index": index, "_id": i}}) + "\n" + json.dumps(d, ensure_ascii=False) + "\n" for i, d in rows)
    r = request("POST", f"_bulk?refresh={refresh}", ndjson=nd, timeout=900)
    if r.get("errors"):
        bad = [it for it in r["items"] if it.get("index", {}).get("error")][:3]
        raise RuntimeError(f"bulk index into {index} had errors: {json.dumps(bad)[:800]}")
    return r


def delete_by_query(index: str, query: dict) -> dict:
    return request("POST", f"{index}/_delete_by_query?refresh=true&conflicts=proceed", {"query": query})
