"""pattern_curator orchestrator (C3 / D3, contracts.md §3.6).

Ties the deterministic sub-tasks + the one LLM judgment into a single
incremental run. NOT an ADK LlmAgent — a standalone batch job (cron / Cloud Run
Job / Elastic Workflow), no synchronous dependency on the real-time flow.

Run order (each independently reviewable; all but dedup's judgment are pure code):
  1. SCAN     incrementally from the last-run watermark (§3.6.1).
  2. HYGIENE  structural + referential event-id repair, support_count recompute,
              timestamp fill (hygiene.py); persist repaired rows via OCC.
  3. EVICTION select low-support / stale-orphan rows (eviction.py); targeted
              delete.
  4. DEDUP    recall (deterministic) + one LLM "same phenomenon?" judgment per
              pair + OCC merge (dedup.py).
  5. WATERMARK advance to the max last_updated_at seen, so the next run is
              O(new-since-now).

Writes go IN PLACE to the concrete drift_patterns index with optimistic
concurrency. The C1 shadow-index/alias rebuild (manage_pattern_alias.py) is only
for the rare human-triggered taxonomy backfill, not this routine job (§3.6.1).

CLI (from agents/):
    uv run python -m pattern_curator.curator                # dry-run, full scan
    uv run python -m pattern_curator.curator --apply        # write
    uv run python -m pattern_curator.curator --since 2026-05-30T00:00:00Z
    uv run python -m pattern_curator.curator --apply --evict-min-support 1
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_AGENTS_ROOT, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_AGENTS_ROOT / ".env")

from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402
from pattern_curator import dedup, eviction, hygiene  # noqa: E402
from pattern_curator.es_ops import PatternStore  # noqa: E402

# Curator watermark lives in a tiny state index, one row, mirroring the
# dispatch_state pattern (§2.2.7). Kept separate from dispatch_state so the two
# decoupled jobs never share a row.
CURATOR_STATE_INDEX = "curator_state"
CURATOR_STATE_ID = "pattern_curator"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class CuratorReport:
    scanned: int = 0
    hygiene_repaired: list[dict[str, Any]] = field(default_factory=list)
    evicted: list[tuple[str, str]] = field(default_factory=list)
    dedup: dedup.DedupReport | None = None
    new_watermark: str | None = None
    applied: bool = False

    def summary(self) -> str:
        lines = [
            f"scanned:          {self.scanned}",
            f"hygiene repaired: {len(self.hygiene_repaired)}",
            f"evicted:          {len(self.evicted)}",
        ]
        if self.dedup is not None:
            d = self.dedup
            lines += [
                f"dedup pairs:      {d.pairs_judged} judged, {d.declined} declined",
                f"merges applied:   {len(d.merges_applied)}",
                f"merges proposed:  {len(d.merges_proposed_only)} (dry-run)",
                f"OCC conflicts:    {len(d.conflicts)}",
                f"judgments capped: {d.judgments_capped} "
                f"(more pairs deferred to next run)" if d.judgments_capped else
                f"judgments capped: False",
            ]
        lines.append(f"new watermark:    {self.new_watermark}")
        lines.append(f"applied:          {self.applied}")
        return "\n".join(lines)


def _read_watermark(client: ElasticsearchHttpClient) -> str | None:
    try:
        resp = client.request("GET", f"/{CURATOR_STATE_INDEX}/_doc/{CURATOR_STATE_ID}")
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise
    return (resp.get("_source") or {}).get("last_seen_updated_at")


def _write_watermark(client: ElasticsearchHttpClient, watermark: str) -> None:
    doc = {
        "flow_name": CURATOR_STATE_ID,
        "last_seen_updated_at": watermark,
        "last_run_at": _now_iso(),
    }
    client.request(
        "PUT", f"/{CURATOR_STATE_INDEX}/_doc/{CURATOR_STATE_ID}?refresh=wait_for", doc
    )


def _flush_log(msg: str) -> None:
    """Default progress logger: a flushed print so Cloud Run / Cloud Logging
    shows stage progress in real time instead of only the final report (the
    silent-30-min-timeout lesson, 2026-05-31)."""
    print(msg, flush=True)


def run_curator(
    store: PatternStore,
    *,
    apply: bool,
    since: str | None,
    evict_min_support: int = 1,
    stale_orphan_age_iso: str | None = None,
    raw_judge=None,
    now_iso: str | None = None,
    referential_check: bool = True,
    max_judgments: int | None = 50,
    log=_flush_log,
) -> CuratorReport:
    """Execute one incremental curator pass. Pure-ish: all ES access goes
    through the injected `store`, the LLM through `raw_judge`, and the clock via
    `now_iso` — so the whole run is unit-testable with fakes, zero tokens.

    `max_judgments` caps the per-run LLM dedup judgments (the expensive serial
    step); the remainder defers to the next run. `log` receives stage-progress
    lines (default: flushing print) so a long run is observable.
    """
    now = now_iso or _now_iso()
    report = CuratorReport(applied=apply)

    # 1. SCAN
    log(f"[curator] scan since={since!r} apply={apply}")
    patterns = store.scan_since(since)
    report.scanned = len(patterns)
    log(f"[curator] scanned {report.scanned} pattern(s)")
    if not patterns:
        report.new_watermark = since
        return report

    # 2. HYGIENE — one batched referential check for the whole scan, then repair.
    log("[curator] hygiene: checking referential integrity + repairing")
    existing_ids: set[str] | None = None
    if referential_check:
        candidate_ids = hygiene.collect_structural_event_ids(patterns)
        existing_ids = store.existing_event_ids(candidate_ids)

    repaired_patterns: list[dict[str, Any]] = []
    for p in patterns:
        repaired, fixes = hygiene.hygiene_pattern(p, now, existing_ids=existing_ids)
        if hygiene.needs_persist(fixes):
            report.hygiene_repaired.append({"pattern_id": p.get("pattern_id"), "fixes": fixes})
            if apply:
                try:
                    write_resp = store.write_pattern_occ(repaired)
                except RuntimeError as exc:
                    if "409" in str(exc):
                        # concurrent append; skip, next run repairs it
                        report.hygiene_repaired[-1]["note"] = "OCC conflict; skipped"
                        repaired_patterns.append(p)
                        continue
                    raise
                # Refresh the in-memory OCC tokens from the write response so a
                # later dedup merge of THIS SAME row (the common "dirty AND
                # duplicate" case) uses the post-write version, not the stale
                # pre-write one — otherwise the merge would 409 against our own
                # hygiene write and the dirtiest rows would never get merged.
                if isinstance(write_resp, dict):
                    if write_resp.get("_seq_no") is not None:
                        repaired["_seq_no"] = write_resp["_seq_no"]
                    if write_resp.get("_primary_term") is not None:
                        repaired["_primary_term"] = write_resp["_primary_term"]
        repaired_patterns.append(repaired)

    log(f"[curator] hygiene: {len(report.hygiene_repaired)} row(s) need repair")

    # 3. EVICTION — select on post-hygiene rows, targeted delete.
    evictions = eviction.select_evictions(
        repaired_patterns,
        min_support=evict_min_support,
        stale_orphan_age_iso=stale_orphan_age_iso,
    )
    report.evicted = evictions
    evicted_ids = {pid for pid, _ in evictions}
    if apply and evicted_ids:
        store.delete_by_ids(sorted(evicted_ids))
    log(f"[curator] eviction: {len(evicted_ids)} row(s) selected")

    # 4. DEDUP — on the rows that survived eviction (bounded; see run_dedup).
    survivors = [p for p in repaired_patterns if p.get("pattern_id") not in evicted_ids]
    report.dedup = dedup.run_dedup(
        store, survivors, now, apply=apply, raw_judge=raw_judge,
        max_judgments=max_judgments, log=log,
    )

    # 5. WATERMARK — advance to the max last_updated_at observed this scan,
    # UNLESS dedup was capped (pairs remain in this same window). Advancing past
    # a capped window would strand the un-judged pairs forever, so we hold the
    # watermark at `since` and let the next run finish the window. (Merges stamp
    # last_updated_at=now, so a naive max() would otherwise jump the cursor to
    # now and skip the leftovers.)
    if report.dedup is not None and report.dedup.judgments_capped:
        report.new_watermark = since
        log("[curator] dedup capped -> holding watermark at "
            f"{since!r} so the next run finishes this window")
    else:
        seen = [p.get("last_updated_at") for p in patterns
                if isinstance(p.get("last_updated_at"), str)]
        report.new_watermark = max(seen) if seen else since
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--apply", action="store_true", help="Actually write. Default dry-run.")
    p.add_argument("--since", default=None,
                   help="ISO watermark override; default = stored watermark (full scan if none).")
    p.add_argument("--evict-min-support", type=int, default=1,
                   help="Evict patterns with support_count below this (default 1 = empty rows only).")
    p.add_argument("--stale-orphan-before", default=None,
                   help="ISO cutoff; support<=1 rows older than this are evicted as stale orphans.")
    p.add_argument("--no-referential-check", action="store_true",
                   help="Skip the drift_events existence check (structural hygiene only).")
    p.add_argument("--no-watermark", action="store_true",
                   help="Do not read/advance the stored watermark (one-off run).")
    p.add_argument("--max-judgments", type=int, default=50,
                   help="Cap on LLM dedup judgments per run (the expensive serial step). "
                        "Default 50 keeps a run well inside the Cloud Run task timeout; "
                        "leftover pairs defer to the next run. Pass 0 for unbounded.")
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        client = ElasticsearchHttpClient()
        store = PatternStore(client=client)

        since = args.since
        if since is None and not args.no_watermark:
            since = _read_watermark(client)

        report = run_curator(
            store,
            apply=args.apply,
            since=since,
            evict_min_support=args.evict_min_support,
            stale_orphan_age_iso=args.stale_orphan_before,
            referential_check=not args.no_referential_check,
            max_judgments=(None if args.max_judgments == 0 else args.max_judgments),
        )

        print(report.summary())
        if report.hygiene_repaired:
            print("\nhygiene detail:")
            for h in report.hygiene_repaired:
                print(f"  {h['pattern_id']}: {h['fixes']}")
        if report.evicted:
            print("\neviction detail:")
            for pid, reason in report.evicted:
                print(f"  {pid}: {reason}")
        if report.dedup and (report.dedup.merges_applied or report.dedup.merges_proposed_only):
            print("\nmerge detail:")
            for m in report.dedup.merges_applied + report.dedup.merges_proposed_only:
                tag = "APPLIED" if m.applied else "PROPOSED"
                print(f"  [{tag}] {m.loser_id} -> {m.survivor_id} "
                      f"(support={m.new_support_count}): {m.rationale}")

        # advance watermark only on a real apply run
        if args.apply and not args.no_watermark and report.new_watermark:
            _write_watermark(client, report.new_watermark)
            print(f"\nwatermark advanced to {report.new_watermark}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
