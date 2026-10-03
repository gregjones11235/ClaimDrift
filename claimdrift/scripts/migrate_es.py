"""Copy ClaimDrift indices from one Elasticsearch to another (部署方案 O4: local docker node -> Elasticsearch Serverless).

  uv run python claimdrift/scripts/migrate_es.py --from http://localhost:9200 --to "$ELASTIC_ENDPOINT" \
      --indices drift_events,affected_citations,citation_runs,notification_log,claims [--dry-run]

Documents keep their _id, so a re-run overwrites instead of duplicating. The target indices must already exist (created
by elastic/scripts/create_indices.py with the same mappings). The target API key is read from ELASTIC_API_KEY (or
--to-api-key); the source is unauthenticated unless --from-api-key is given. Not for indices with semantic_text fields
that hold data: 8.15 keeps inference output inside _source -- rebuild those instead (the self-check index:
`python -m claimdrift selfcheck-index` against the target).
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

BATCH = 500


def request(base: str, key: str | None, method: str, path: str, body=None, ndjson: str | None = None):
    headers = {"Content-Type": "application/x-ndjson" if ndjson is not None else "application/json"}
    if key:
        headers["Authorization"] = f"ApiKey {key}"
    data = ndjson.encode() if ndjson is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"{base.rstrip('/')}/{path.lstrip('/')}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode('utf-8', 'ignore')[:800]}") from e


def scan(base: str, key: str | None, index: str):
    r = request(base, key, "POST", f"{index}/_search?scroll=2m", {"size": BATCH, "sort": ["_doc"]})
    sid = r.get("_scroll_id")
    try:
        while r["hits"]["hits"]:
            yield from r["hits"]["hits"]
            r = request(base, key, "POST", "_search/scroll", {"scroll": "2m", "scroll_id": sid})
            sid = r.get("_scroll_id", sid)
    finally:
        if sid:
            try:
                request(base, key, "DELETE", "_search/scroll", {"scroll_id": sid})
            except RuntimeError:
                pass


def count(base: str, key: str | None, index: str) -> int:
    return request(base, key, "GET", f"{index}/_count")["count"]


def copy_index(src: str, src_key: str | None, dst: str, dst_key: str | None, index: str, dry_run: bool) -> dict:
    n, errors, lines = 0, [], []

    def flush():
        if not lines or dry_run:
            lines.clear()
            return
        r = request(dst, dst_key, "POST", "_bulk?refresh=wait_for", ndjson="\n".join(lines) + "\n")
        if r.get("errors"):
            errors.extend(i["index"] for i in r["items"] if i["index"].get("error"))
        lines.clear()

    for h in scan(src, src_key, index):
        lines += [json.dumps({"index": {"_index": index, "_id": h["_id"]}}), json.dumps(h["_source"], ensure_ascii=False)]
        n += 1
        if len(lines) >= 2 * BATCH:
            flush()
    flush()
    out = {"index": index, "source_docs": n, "errors": len(errors)}
    if errors:
        out["first_errors"] = [e.get("error") for e in errors[:3]]
    if not dry_run:
        out["target_docs"] = count(dst, dst_key, index)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default="http://localhost:9200")
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--indices", default="drift_events,affected_citations,citation_runs,notification_log,claims")
    ap.add_argument("--from-api-key", default=None)
    ap.add_argument("--to-api-key", default=os.environ.get("ELASTIC_API_KEY"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    for index in [i.strip() for i in a.indices.split(",") if i.strip()]:
        print(json.dumps(copy_index(a.src, a.from_api_key, a.dst, a.to_api_key, index, a.dry_run), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
