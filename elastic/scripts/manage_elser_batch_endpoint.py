"""Manage the curator's dedicated ELSER inference endpoint
(`claimdrift-elser-batch`).

This is the C1 / Part-D4 deliverable from
[docs/memory_loop_v2_design.md](../../docs/memory_loop_v2_design.md) B.2 and
[contracts.md §2.2.5](../../docs/contracts.md) "Dual inference endpoint (v2)".

Why this endpoint exists
------------------------
`drift_patterns.pattern_description` is a `semantic_text` field whose
`inference_id` is fixed at index time to `.elser-2-elastic`. Real-time
retrieval (`search_drift_patterns`) and any `pattern_curator` re-embedding
would otherwise contend on that *same* ELSER endpoint — and re-embedding many
descriptions at once can spike it and slow live retrieval. The only real
performance risk the v2 design identified is this shared-endpoint contention,
NOT generic CPU/IO. So the curator gets its own endpoint instance, backed by
the same ELSER-2 model, with independent capacity/concurrency. The shadow
index `drift_patterns_v2` binds its `pattern_description` to THIS endpoint
(see drift_patterns_v2.json), so curator writes re-embed off the batch
endpoint while live reads stay on `.elser-2-elastic`.

Provisioning shape (verified against the live serverless cluster 2026-05-30)
---------------------------------------------------------------------------
ELSER-2 on Elastic Serverless is exposed through the EIS-managed `elastic`
inference service (same family as the preconfigured `.elser-2-elastic`). The
`elasticsearch` service variant needs a deployable model + ML allocations that
serverless does not let users provision. So the accepted payload is:

    PUT /_inference/sparse_embedding/claimdrift-elser-batch
    { "service": "elastic", "service_settings": { "model_id": "elser_model_2" } }

The endpoint's capacity is EIS-managed; "independent capacity/concurrency"
comes from it being a *separate* endpoint instance, not from us tuning
allocations (which serverless does not expose for `elastic`-service ELSER).

Usage (WSL, from agents/ so the `agents/.env` ELASTIC_* vars load):
    uv run python ../elastic/scripts/manage_elser_batch_endpoint.py status
    uv run python ../elastic/scripts/manage_elser_batch_endpoint.py create --apply
    uv run python ../elastic/scripts/manage_elser_batch_endpoint.py delete --apply

`create` is idempotent: if the endpoint already exists it is left untouched.
Dry-run is the default for create/delete; pass --apply to actually write.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "agents"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / "agents" / ".env")

from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

# Contract constant: the dedicated batch endpoint id (contracts.md §2.2.5).
BATCH_ENDPOINT_ID = "claimdrift-elser-batch"
TASK_TYPE = "sparse_embedding"

# The accepted serverless payload (see module docstring). Same ELSER-2 model as
# `.elser-2-elastic`; a distinct endpoint instance gives independent capacity.
ENDPOINT_BODY = {
    "service": "elastic",
    "service_settings": {"model_id": "elser_model_2"},
}

_ENDPOINT_PATH = f"/_inference/{TASK_TYPE}/{BATCH_ENDPOINT_ID}"


def _get_endpoint(client: ElasticsearchHttpClient) -> dict | None:
    """Return the endpoint config if it exists, else None."""
    try:
        resp = client.request("GET", _ENDPOINT_PATH)
    except RuntimeError as exc:
        if "404" in str(exc) or "not_found" in str(exc):
            return None
        raise
    endpoints = resp.get("endpoints")
    if isinstance(endpoints, list) and endpoints:
        return endpoints[0]
    return resp or None


def cmd_status(client: ElasticsearchHttpClient, _args: argparse.Namespace) -> int:
    ep = _get_endpoint(client)
    if ep is None:
        print(f"Endpoint '{BATCH_ENDPOINT_ID}' does NOT exist.")
        return 1
    print(f"Endpoint '{BATCH_ENDPOINT_ID}' exists:")
    print(json.dumps(ep, indent=2))
    return 0


def cmd_create(client: ElasticsearchHttpClient, args: argparse.Namespace) -> int:
    existing = _get_endpoint(client)
    if existing is not None:
        print(f"Endpoint '{BATCH_ENDPOINT_ID}' already exists — nothing to do (idempotent).")
        return 0

    if not args.apply:
        print(f"Dry run. Would create endpoint '{BATCH_ENDPOINT_ID}':")
        print(f"  PUT {_ENDPOINT_PATH}")
        print(f"  body: {json.dumps(ENDPOINT_BODY)}")
        print("Pass --apply to create it.")
        return 0

    resp = client.request("PUT", _ENDPOINT_PATH, ENDPOINT_BODY)
    print(f"Created endpoint '{BATCH_ENDPOINT_ID}'.")
    print(json.dumps(resp, indent=2)[:600])
    return 0


def cmd_delete(client: ElasticsearchHttpClient, args: argparse.Namespace) -> int:
    existing = _get_endpoint(client)
    if existing is None:
        print(f"Endpoint '{BATCH_ENDPOINT_ID}' does not exist — nothing to delete.")
        return 0

    if not args.apply:
        print(f"Dry run. Would DELETE endpoint '{BATCH_ENDPOINT_ID}'.")
        print("  Note: deletion fails if a semantic_text field still binds this")
        print("  inference_id (e.g. drift_patterns_v2). Drop that index first.")
        print("Pass --apply to delete it.")
        return 0

    resp = client.request("DELETE", _ENDPOINT_PATH)
    print(f"Deleted endpoint '{BATCH_ENDPOINT_ID}'.")
    print(json.dumps(resp, indent=2)[:300])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show whether the endpoint exists.")
    p_status.set_defaults(func=cmd_status)

    p_create = sub.add_parser("create", help="Create the endpoint (idempotent).")
    p_create.add_argument("--apply", action="store_true", help="Actually create it.")
    p_create.set_defaults(func=cmd_create)

    p_delete = sub.add_parser("delete", help="Delete the endpoint.")
    p_delete.add_argument("--apply", action="store_true", help="Actually delete it.")
    p_delete.set_defaults(func=cmd_delete)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        client = ElasticsearchHttpClient()
        raise SystemExit(args.func(client, args))
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
