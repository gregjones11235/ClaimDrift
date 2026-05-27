"""Capture a real supervisor stream for dispatcher (A) to develop against.

Hits the deployed supervisor reasoning engine with the canonical HCQ
preprint+published demo envelope and dumps every event in the stream to
`apps/dispatcher/_stub_stream.json` (gitignored — A puts it next to main.py).

Usage:
    cd ~/claim_drift
    uv run python agents/supervisor_agent/scripts/capture_stream.py

Prereqs:
    - GCP ADC configured (gcloud auth application-default login)
    - Supervisor deployed; SUPERVISOR_REASONING_ENGINE_ID hard-coded below
      (update if you redeploy with a new id).

Output:
    Writes to apps/dispatcher/_stub_stream.json. A's dispatcher reads this
    when USE_STUB_STREAM=1.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import vertexai
from vertexai import agent_engines

PROJECT = "tensile-topic-496519-i1"
LOCATION = "us-central1"
SUPERVISOR_REASONING_ENGINE_ID = "7816826734824652800"

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_PATH = REPO_ROOT / "apps" / "dispatcher" / "_stub_stream.json"

# Canonical HCQ demo envelope (from elastic/demo_seed/preprints.json — the v3
# preprint + its published version). Same envelope that validated the first
# supervisor smoke test 2026-05-24.
ENVELOPE = {
    "preprint": {
        "doi": "10.1101/2024.01.15.123456",
        "version": "v3",
        "title": "Hydroxychloroquine and viral load dynamics in early COVID-19",
        "abstract": "Hydroxychloroquine reduced viral load by 45% in early COVID-19 patients.",
        "conclusion": "The treatment reduced viral load substantially in the studied cohort.",
    },
    "published": {
        "doi": "10.1016/j.cell.2024.05.001",
        "version": "published",
        "title": "Hydroxychloroquine and viral load dynamics in early COVID-19",
        "abstract": "Hydroxychloroquine was associated with a 12% viral load reduction, with uncertainty across subgroups.",
        "conclusion": "The treatment may reduce viral load modestly in selected patients.",
    },
}


async def _heartbeat(events: list[dict], stop: asyncio.Event) -> None:
    """Print elapsed seconds every 10s so we can tell 'slow' from 'hung'."""
    import time
    t0 = time.monotonic()
    last_count = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            print(
                f"  ... heartbeat: {elapsed:5.1f}s, {len(events)} events so far "
                f"(+{len(events) - last_count} since last beat)",
                file=sys.stderr,
            )
            last_count = len(events)


async def main() -> None:
    vertexai.init(project=PROJECT, location=LOCATION)
    supervisor = agent_engines.get(SUPERVISOR_REASONING_ENGINE_ID)

    message = json.dumps(ENVELOPE, ensure_ascii=False)
    user_id = f"stub-capture::{ENVELOPE['preprint']['doi']}"

    events: list[dict] = []
    stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat(events, stop))

    print(f"Streaming supervisor events to {OUTPUT_PATH} ...", file=sys.stderr)
    try:
        async for chunk in supervisor.async_stream_query(message=message, user_id=user_id):
            events.append(chunk)
            # Pull the author/type for a one-line progress trace
            author = chunk.get("author", "?")
            parts = (chunk.get("content") or {}).get("parts") or []
            kind = "text"
            for p in parts:
                if "function_call" in p:
                    kind = f"call:{p['function_call'].get('name', '?')}"
                    break
                if "function_response" in p:
                    kind = f"resp:{p['function_response'].get('name', '?')}"
                    break
            print(f"  [{len(events):3d}] {author:25s} {kind}", file=sys.stderr)
    finally:
        stop.set()
        await heartbeat_task

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(events)} events to {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
