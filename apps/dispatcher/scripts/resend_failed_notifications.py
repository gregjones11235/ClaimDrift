"""Resend every failed notification_log row via Gmail, retrying in rounds.

Why this exists
---------------
A bulk backfill left a batch of notification_log rows stuck at status="failed".
The errors are all transient transport failures — Gmail 429 ("User-rate limit
exceeded"), BrokenPipeError, SSLError — i.e. the send happened to fail at that
instant, not because the message is undeliverable. The right fix for that class
of error is not cleverer single-send retry logic, it's simply: try again a
while later. This script does exactly that, in rounds.

How it works
------------
Each ROUND:
  1. Scans notification_log for status="failed" (paged via search_after).
  2. For each row: injects recipient_email = --recipient, then calls
     main.send_and_update(), which sends through the live Gmail path and, on
     success, rewrites the row to status="sent" / sent_at / recipient_email; on
     failure it stays "failed" with the new error. Sends are paced
     (--send-interval) so one round doesn't itself provoke 429s.
  3. Refreshes the index and counts how many are still failed.

Between rounds the script SLEEPS for --round-wait, then runs another round.
Because each round only ever picks up rows still at status="failed", anything
that succeeded is never touched again, and anything that hit a transient error
gets retried in the next round (a while later) — which is what absorbs the
unavoidable network blips. It stops when zero rows remain failed, or after
--max-rounds rounds (a backstop so a *permanent* problem — expired creds, quota
exhausted — doesn't loop forever).

Idempotent & safe to Ctrl-C: rerunning just resumes from whatever is still
failed.

Usage (from repo root, in WSL):
    cd ~/claim_drift/apps/dispatcher
    set -a; source .env; set +a            # ELASTIC_* + GCP_PROJECT for Secret Manager
    source .venv/bin/activate

    # Preview what would be sent (no Gmail calls, no ES writes):
    python scripts/resend_failed_notifications.py --dry-run

    # Resend everything, retrying every 5 min for up to 10 rounds (defaults):
    python scripts/resend_failed_notifications.py

    # Tune cadence / cap / recipient / test batch:
    python scripts/resend_failed_notifications.py \
        --round-wait 60 --max-rounds 20 --recipient you@example.com --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make `import main` work when run from apps/dispatcher/scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402

DEFAULT_RECIPIENT = "claimdriftnotifier@gmail.com"
DEFAULT_ROUND_WAIT_SEC = 300.0   # 5 minutes between retry rounds
DEFAULT_MAX_ROUNDS = 10
DEFAULT_SEND_INTERVAL_SEC = 1.1  # in-round pacing; Gmail per-user quota ~1/s
PAGE_SIZE = 100


async def _scan_failed(limit: int | None) -> list[tuple[str, dict[str, Any]]]:
    """Return [(doc_id, _source), ...] for status=failed rows, oldest first.

    Pages with search_after so it scales past the 10k window without a scroll
    context. Sorts by affected_citation_id (a unique keyword — the doc's natural
    key) which gives a total, stable order for search_after. We deliberately do
    NOT sort on _id: this cluster disallows fielddata on _id
    (indices.id_field_data.enabled is off), which 400s the query.
    """
    es = main.get_es()
    rows: list[tuple[str, dict[str, Any]]] = []
    search_after: list[Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "index": "notification_log",
            "size": PAGE_SIZE,
            "query": {"term": {"status": "failed"}},
            "sort": [{"affected_citation_id": "asc"}],
        }
        if search_after is not None:
            kwargs["search_after"] = search_after
        resp = await es.search(**kwargs)
        hits = resp["hits"]["hits"]
        if not hits:
            break
        for h in hits:
            rows.append((h["_id"], h["_source"]))
            if limit is not None and len(rows) >= limit:
                return rows
        search_after = hits[-1]["sort"]
        if len(hits) < PAGE_SIZE:
            break
    return rows


async def _run_round(recipient: str, limit: int | None,
                     send_interval: float) -> tuple[int, int]:
    """Send all currently-failed rows once. Returns (sent, still_failed)."""
    rows = await _scan_failed(limit)
    if not rows:
        return (0, 0)
    print(f"  {len(rows)} row(s) at status='failed' this round.")

    sent = 0
    still_failed = 0
    for i, (doc_id, src) in enumerate(rows):
        if i > 0:
            await asyncio.sleep(send_interval)
        # The stored row has recipient_email=null (citation_finder v0 doesn't
        # resolve author emails); inject the demo inbox so send_and_update has
        # an address. On success it writes status=sent + sent_at +
        # recipient_email; on failure it rewrites status=failed with the error.
        doc = dict(src)
        doc["recipient_email"] = recipient
        await main.send_and_update(doc_id, doc)

        resp = await main.get_es().get(index="notification_log", id=doc_id)
        if resp["_source"].get("status") == "sent":
            sent += 1
        else:
            still_failed += 1
        if (i + 1) % 25 == 0:
            print(f"    ... {i + 1}/{len(rows)} ({sent} sent, {still_failed} failed)")

    # send_and_update writes with refresh=False for throughput; force a refresh
    # so the next round's scan (and the dashboard) sees the new statuses.
    await main.get_es().indices.refresh(index="notification_log")
    return (sent, still_failed)


async def _dry_run(recipient: str, limit: int | None) -> None:
    rows = await _scan_failed(limit)
    print(f"Found {len(rows)} notification_log rows at status='failed'.")
    print(f"--dry-run: would resend all of them to {recipient!r}. "
          f"No Gmail calls, no ES writes.\n")
    for i, (doc_id, src) in enumerate(rows[:10], 1):
        print(f"  [{i}] {doc_id}")
        print(f"       subject: {(src.get('subject') or '')[:70]}")
        print(f"       was:     error={(src.get('error_message') or '')[:60]}")
    if len(rows) > 10:
        print(f"  ... and {len(rows) - 10} more")
    await main.get_es().close()


async def resend(recipient: str, limit: int | None, dry_run: bool,
                 round_wait: float, max_rounds: int,
                 send_interval: float) -> None:
    if dry_run:
        await _dry_run(recipient, limit)
        return

    total_sent = 0
    for round_no in range(1, max_rounds + 1):
        print(f"\n=== Round {round_no}/{max_rounds} "
              f"({datetime.now(timezone.utc).isoformat()}) ===")
        sent, still_failed = await _run_round(recipient, limit, send_interval)
        total_sent += sent
        print(f"  round result: {sent} sent, {still_failed} still failed "
              f"(cumulative sent: {total_sent}).")

        if still_failed == 0:
            print("\nAll failed notifications resent successfully. Done.")
            break
        if round_no == max_rounds:
            print(f"\nReached --max-rounds={max_rounds} with {still_failed} "
                  f"still failed. These are likely a persistent problem (expired "
                  f"creds, exhausted quota, bad payload) — inspect before rerunning.")
            break
        print(f"  waiting {round_wait:.0f}s before the next round "
              f"(transient errors get retried a while later)...")
        await asyncio.sleep(round_wait)

    await main.get_es().close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--recipient", default=DEFAULT_RECIPIENT,
                   help=f"Where to send every resend (default: {DEFAULT_RECIPIENT})")
    p.add_argument("--round-wait", type=float, default=DEFAULT_ROUND_WAIT_SEC,
                   help=f"Seconds to wait between retry rounds (default: {DEFAULT_ROUND_WAIT_SEC:.0f})")
    p.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS,
                   help=f"Stop after this many rounds (default: {DEFAULT_MAX_ROUNDS})")
    p.add_argument("--send-interval", type=float, default=DEFAULT_SEND_INTERVAL_SEC,
                   help=f"Seconds between sends within a round (default: {DEFAULT_SEND_INTERVAL_SEC})")
    p.add_argument("--limit", type=int, default=None,
                   help="Only process the first N failed rows per round (test batch)")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be sent; make no Gmail calls or ES writes")
    return p.parse_args()


if __name__ == "__main__":
    a = _parse_args()
    asyncio.run(resend(a.recipient, a.limit, a.dry_run,
                       a.round_wait, a.max_rounds, a.send_interval))
