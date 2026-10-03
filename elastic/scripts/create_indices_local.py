"""Create the ClaimDrift indices on a LOCAL self-managed Elasticsearch (8.15.x).

The authoritative mappings in elastic/mappings/ target Elastic Serverless:
  - semantic_text fields use the hosted `.elser-2-elastic` inference endpoint
  - semantic_text fields may declare `copy_to`

Neither exists on self-managed 8.15, so this script rewrites the mappings
in memory (the JSON files stay authoritative for Serverless):
  - inference_id  ->  the local endpoint (default `elser-local`)
  - `copy_to` is dropped from semantic_text fields; WRITERS MUST fill any
    mirror text field themselves

Prerequisites (one-time, see agent项目升级改造.md §ELSER):
  docker run ... -e xpack.ml.enabled=true elasticsearch:8.15.3      # node has the `ml` role
  POST /_license/start_trial?acknowledge=true                        # ML needs trial/Platinum+, NOT basic
  PUT  /_inference/sparse_embedding/elser-local {"service":"elser","service_settings":{"num_allocations":1,"num_threads":2}}

Usage:
  python elastic/scripts/create_indices_local.py [--endpoint http://localhost:9200] [--inference-id elser-local] [--recreate claims,drift_events]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import urllib.error
import urllib.request

MAPPINGS = pathlib.Path(__file__).resolve().parents[1] / "mappings"
SKIP: set[str] = set()  # mapping files not to create locally (none at present)


def es(base: str, method: str, path: str, body=None):
    req = urllib.request.Request(f"{base}/{path.lstrip('/')}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def localize(node, inference_id: str):
    if isinstance(node, dict):
        if node.get("type") == "semantic_text":
            node["inference_id"] = inference_id
            node.pop("copy_to", None)
        for v in node.values():
            localize(v, inference_id)
    elif isinstance(node, list):
        for v in node:
            localize(v, inference_id)
    return node


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://localhost:9200")
    ap.add_argument("--inference-id", default="elser-local")
    ap.add_argument("--recreate", default="", help="comma-separated indices to delete and recreate")
    a = ap.parse_args()
    base = a.endpoint.rstrip("/")
    recreate = {x for x in a.recreate.split(",") if x}
    st, inf = es(base, "GET", f"_inference/sparse_embedding/{a.inference_id}")
    if st != 200:
        raise SystemExit(f"inference endpoint {a.inference_id!r} not found ({st}); create it first (see module docstring)")
    for path in sorted(MAPPINGS.glob("*.json")):
        name = path.stem
        if name in SKIP:
            continue
        body = localize(json.loads(path.read_text(encoding="utf-8")), a.inference_id)
        if name in recreate:
            es(base, "DELETE", name)
        st, res = es(base, "PUT", name, body)
        if st == 200:
            print(f"created   {name}")
        elif "resource_already_exists_exception" in json.dumps(res):
            st2, res2 = es(base, "PUT", f"{name}/_mapping", body["mappings"])
            print(f"exists    {name}  (mapping update: {'ok' if st2 == 200 else json.dumps(res2)[:200]})")
        else:
            print(f"FAILED    {name}: {json.dumps(res)[:300]}")


if __name__ == "__main__":
    main()
