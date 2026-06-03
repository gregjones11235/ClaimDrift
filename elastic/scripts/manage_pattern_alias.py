"""Manage the `drift_patterns_read` alias and the curator's blue-green rebuild.

This is the C1 / Part-D4 read-side deliverable from
[docs/memory_loop_v2_design.md](../../docs/memory_loop_v2_design.md) B.2 and
[contracts.md §2.2.5](../../docs/contracts.md).

The architecture (and the scope boundary this script deliberately enforces)
---------------------------------------------------------------------------
Real-time retrieval (`search_drift_patterns`, called by drift_analyzer and
memory_synthesizer) must read through an ALIAS, not a concrete index, so the
`pattern_curator` can rebuild the patterns index off-line and swap it in
atomically without the live read path noticing. That swap is an ES metadata
operation — milliseconds, atomic, zero re-inference — so it has zero
performance impact. The ELSER contention that v2 actually worried about is
handled separately by the dedicated `claimdrift-elser-batch` endpoint
(see manage_elser_batch_endpoint.py).

READS go through the alias. WRITES do NOT.
  - memory_synthesizer's create/update_drift_pattern keep writing the concrete
    `drift_patterns` index directly. The design only ever specified
    "real-time *reads* via alias" (design B.2); routing writes through an alias
    would force changes to the Agent Builder write tools (a cross-boundary edit
    this project has repeatedly been burned by) for no benefit: synthesizer
    writes are sparse and rare, and rebuild-window write consistency is the
    curator's job via optimistic concurrency (if_seq_no/if_primary_term) +
    incremental catch-up during reindex (design B.1), not an alias concern.

Why an alias named `drift_patterns_read` rather than renaming the index
-----------------------------------------------------------------------
`drift_patterns` is already a concrete index holding real data, and an alias
cannot share a name with an existing index. Migrating the data to
`drift_patterns_v1` so `drift_patterns` could become the alias would require a
reindex + delete of live data (downtime + mis-delete risk). Adding a NEW read
alias that points at the untouched `drift_patterns` is zero-migration,
zero-downtime, and trivially reversible.

Commands
--------
  status      Show the alias and which concrete index it points at.
  init        Create `drift_patterns_read` -> `drift_patterns` (idempotent).
  rebuild     Blue-green: (1) create shadow index `drift_patterns_v2` from the
              committed mapping (binds pattern_description to
              claimdrift-elser-batch), (2) reindex from the alias's current
              target into the shadow index (re-embeds via the batch endpoint),
              (3) atomically swap the alias to the shadow index in ONE
              _aliases call. The previous index is left intact for rollback.
  rollback    Atomically point the alias back at `drift_patterns`.

Usage (WSL, from agents/ so agents/.env loads):
    uv run python ../elastic/scripts/manage_pattern_alias.py status
    uv run python ../elastic/scripts/manage_pattern_alias.py init --apply
    uv run python ../elastic/scripts/manage_pattern_alias.py rebuild --apply
    uv run python ../elastic/scripts/manage_pattern_alias.py rollback --apply

All mutating commands dry-run by default; pass --apply to write.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "agents"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / "agents" / ".env")

from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

# --- contract constants (contracts.md §2.2.5) -------------------------------
PRIMARY_INDEX = "drift_patterns"           # live concrete index, real-time-endpoint bound
SHADOW_INDEX = "drift_patterns_v2"         # batch-endpoint bound (drift_patterns_v2.json)
READ_ALIAS = "drift_patterns_read"         # real-time reads go through this

_MAPPINGS_DIR = _ROOT / "elastic" / "mappings"
_SHADOW_MAPPING_FILE = _MAPPINGS_DIR / f"{SHADOW_INDEX}.json"


# --- low-level helpers ------------------------------------------------------
def _index_exists(client: ElasticsearchHttpClient, name: str) -> bool:
    try:
        client.request("GET", f"/{quote(name)}")
        return True
    except RuntimeError as exc:
        if "404" in str(exc):
            return False
        raise


def _alias_targets(client: ElasticsearchHttpClient, alias: str) -> list[str]:
    """Concrete indices the alias currently points at ([] if alias absent)."""
    try:
        resp = client.request("GET", f"/_alias/{quote(alias)}")
    except RuntimeError as exc:
        if "404" in str(exc):
            return []
        raise
    return sorted(resp.keys())


def _count(client: ElasticsearchHttpClient, index_or_alias: str) -> int:
    resp = client.request("POST", f"/{quote(index_or_alias)}/_count", {})
    return int(resp.get("count", 0))


def _atomic_swap(client: ElasticsearchHttpClient, alias: str, remove_from: list[str], add_to: str) -> dict:
    """One _aliases call = atomic remove(old) + add(new). No read gap."""
    actions: list[dict] = []
    for idx in remove_from:
        actions.append({"remove": {"index": idx, "alias": alias}})
    actions.append({"add": {"index": add_to, "alias": alias}})
    return client.request("POST", "/_aliases", {"actions": actions})


# --- commands ---------------------------------------------------------------
def cmd_status(client: ElasticsearchHttpClient, _args: argparse.Namespace) -> int:
    print(f"primary index '{PRIMARY_INDEX}': "
          f"{'exists' if _index_exists(client, PRIMARY_INDEX) else 'MISSING'}"
          f" (docs={_count(client, PRIMARY_INDEX) if _index_exists(client, PRIMARY_INDEX) else 'n/a'})")
    print(f"shadow index  '{SHADOW_INDEX}': "
          f"{'exists' if _index_exists(client, SHADOW_INDEX) else 'absent'}"
          + (f" (docs={_count(client, SHADOW_INDEX)})" if _index_exists(client, SHADOW_INDEX) else ""))
    targets = _alias_targets(client, READ_ALIAS)
    if targets:
        print(f"read alias    '{READ_ALIAS}' -> {targets} (docs={_count(client, READ_ALIAS)})")
    else:
        print(f"read alias    '{READ_ALIAS}': NOT created yet")
    return 0


def cmd_init(client: ElasticsearchHttpClient, args: argparse.Namespace) -> int:
    if not _index_exists(client, PRIMARY_INDEX):
        print(f"ERROR: primary index '{PRIMARY_INDEX}' does not exist. "
              f"Create it first (elastic/scripts/create_indices.py).", file=sys.stderr)
        return 1

    targets = _alias_targets(client, READ_ALIAS)
    if targets == [PRIMARY_INDEX]:
        print(f"Alias '{READ_ALIAS}' already points at '{PRIMARY_INDEX}' — nothing to do.")
        return 0
    if targets:
        print(f"Alias '{READ_ALIAS}' currently points at {targets}, not '{PRIMARY_INDEX}'. "
              f"Use 'rebuild' or 'rollback' to move it deliberately.")
        return 1

    if not args.apply:
        print(f"Dry run. Would create alias '{READ_ALIAS}' -> '{PRIMARY_INDEX}'.")
        print("Pass --apply to create it.")
        return 0

    _atomic_swap(client, READ_ALIAS, remove_from=[], add_to=PRIMARY_INDEX)
    print(f"Created alias '{READ_ALIAS}' -> '{PRIMARY_INDEX}'.")
    return 0


def _create_shadow_index(client: ElasticsearchHttpClient) -> None:
    body = json.loads(_SHADOW_MAPPING_FILE.read_text())
    client.put_index(SHADOW_INDEX, body)


def cmd_rebuild(client: ElasticsearchHttpClient, args: argparse.Namespace) -> int:
    targets = _alias_targets(client, READ_ALIAS)
    if not targets:
        print(f"ERROR: alias '{READ_ALIAS}' does not exist. Run 'init --apply' first.", file=sys.stderr)
        return 1
    source = targets[0]
    if source == SHADOW_INDEX:
        print(f"Alias already points at '{SHADOW_INDEX}'. Rebuild target would be the same "
              f"index — nothing to do. (Drop/recreate {SHADOW_INDEX} if you want a fresh rebuild.)")
        return 0

    src_docs = _count(client, source)
    if not args.apply:
        print("Dry run. Blue-green rebuild plan:")
        print(f"  1. create shadow index '{SHADOW_INDEX}' from {_SHADOW_MAPPING_FILE.name}")
        print(f"     (pattern_description -> inference_id: claimdrift-elser-batch)")
        print(f"  2. reindex '{source}' ({src_docs} docs) -> '{SHADOW_INDEX}' "
              f"(re-embeds via batch endpoint)")
        print(f"  3. atomic swap: alias '{READ_ALIAS}'  {source} -> {SHADOW_INDEX}")
        print(f"  ('{source}' is left intact for rollback)")
        print("Pass --apply to execute.")
        return 0

    # 1. shadow index (drop a stale one so the rebuild is clean)
    if _index_exists(client, SHADOW_INDEX):
        print(f"  shadow '{SHADOW_INDEX}' already exists — deleting for a clean rebuild.")
        client.request("DELETE", f"/{quote(SHADOW_INDEX)}")
    _create_shadow_index(client)
    print(f"  [1/3] created shadow index '{SHADOW_INDEX}'.")

    # 2. reindex (synchronous; demo-scale index is tiny). wait_for_completion
    #    so we don't swap before the copy is done.
    reindex_body = {
        "source": {"index": source},
        "dest": {"index": SHADOW_INDEX, "op_type": "create"},
    }
    resp = client.request("POST", "/_reindex?wait_for_completion=true&refresh=true", reindex_body)
    created = resp.get("created", 0)
    failures = resp.get("failures") or []
    if failures:
        print(f"  [2/3] reindex reported {len(failures)} failure(s):", file=sys.stderr)
        for f in failures[:5]:
            print(f"        {json.dumps(f)[:300]}", file=sys.stderr)
        print("  Aborting before swap; alias unchanged. Investigate, then re-run.", file=sys.stderr)
        return 1
    print(f"  [2/3] reindexed {created} doc(s) '{source}' -> '{SHADOW_INDEX}'.")

    # semantic_text re-inference happens during indexing; give the refresh a
    # beat so the count check below is meaningful.
    time.sleep(1.0)
    dst_docs = _count(client, SHADOW_INDEX)
    if dst_docs != src_docs:
        print(f"  WARNING: shadow has {dst_docs} docs but source had {src_docs}. "
              f"Not swapping. Investigate before retrying.", file=sys.stderr)
        return 1

    # 3. atomic swap
    _atomic_swap(client, READ_ALIAS, remove_from=[source], add_to=SHADOW_INDEX)
    print(f"  [3/3] swapped alias '{READ_ALIAS}': {source} -> {SHADOW_INDEX} "
          f"(docs={_count(client, READ_ALIAS)}).")
    print(f"  '{source}' left intact. 'rollback --apply' restores it.")
    return 0


def cmd_rollback(client: ElasticsearchHttpClient, args: argparse.Namespace) -> int:
    targets = _alias_targets(client, READ_ALIAS)
    if not targets:
        print(f"ERROR: alias '{READ_ALIAS}' does not exist.", file=sys.stderr)
        return 1
    if targets == [PRIMARY_INDEX]:
        print(f"Alias already points at '{PRIMARY_INDEX}' — nothing to roll back.")
        return 0
    if not _index_exists(client, PRIMARY_INDEX):
        print(f"ERROR: rollback target '{PRIMARY_INDEX}' does not exist.", file=sys.stderr)
        return 1

    if not args.apply:
        print(f"Dry run. Would swap alias '{READ_ALIAS}': {targets} -> {PRIMARY_INDEX}.")
        print("Pass --apply to execute.")
        return 0

    _atomic_swap(client, READ_ALIAS, remove_from=targets, add_to=PRIMARY_INDEX)
    print(f"Rolled back alias '{READ_ALIAS}': {targets} -> {PRIMARY_INDEX}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    for name, func, mutating in [
        ("status", cmd_status, False),
        ("init", cmd_init, True),
        ("rebuild", cmd_rebuild, True),
        ("rollback", cmd_rollback, True),
    ]:
        p = sub.add_parser(name, help=func.__doc__ or name)
        if mutating:
            p.add_argument("--apply", action="store_true", help="Actually write. Default is dry-run.")
        p.set_defaults(func=func)

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
