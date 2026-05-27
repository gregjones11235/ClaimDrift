"""One-shot Gmail send smoke test.

Runs the dispatcher's send_and_update against a hand-crafted notification_log
row. Useful for validating the Gmail OAuth secrets + send path WITHOUT needing
a real supervisor run that produces affected_citations.

Usage (from apps/dispatcher/, with .env loaded):
    cd ~/claim_drift/apps/dispatcher
    set -a; source .env; set +a
    source .venv/bin/activate
    python scripts/test_gmail_send.py <recipient_email>

The script:
  1. Writes a fake notification_log row (id="test::gmail-smoke")
  2. Calls send_and_update, which sends mail + flips status to "sent"
  3. Prints the resulting notification_log doc so you can confirm status="sent"
     and sent_at is set.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `import main` work when run from apps/dispatcher/scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


async def smoke_test(recipient: str) -> None:
    ac_id = "test::gmail-smoke"
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "affected_citation_id": ac_id,
        "drift_event_id": "test-drift-001",
        "recipient_email": recipient,
        "subject": "[claimdrift dispatcher] Gmail send smoke test",
        "body": (
            "This is an automated smoke test from the claimdrift dispatcher.\n\n"
            f"If you received this at {recipient}, the Gmail OAuth + send path is working.\n\n"
            f"Sent at: {now}\n"
        ),
        "reasoning_trace": "smoke test",
        "status": "drafted",
        "drafted_at": now,
        "sent_at": None,
        "error_message": None,
    }

    print(f"Writing fake notification_log row {ac_id!r}...")
    await main.get_es().index(index="notification_log", id=ac_id, document=doc, refresh="wait_for")

    print(f"Calling send_and_update -> Gmail send to {recipient}...")
    await main.send_and_update(ac_id, doc)

    print(f"Reading back notification_log/_doc/{ac_id}...")
    resp = await main.get_es().get(index="notification_log", id=ac_id)
    src = resp["_source"]
    print(f"  status:      {src.get('status')}")
    print(f"  sent_at:     {src.get('sent_at')}")
    print(f"  recipient:   {src.get('recipient_email')}")
    print(f"  error:       {src.get('error_message')}")

    # Cleanup so notification_log isn't polluted by the smoke row.
    print(f"Deleting smoke test row...")
    await main.get_es().delete(index="notification_log", id=ac_id, refresh="wait_for")

    await main.get_es().close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/test_gmail_send.py <recipient_email>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(smoke_test(sys.argv[1]))
