"""ClaimDrift Playground backend.

Serves the live 5-agent orchestration (implemented in orchestration.py; runs the local `claimdrift` pipeline,
2026-10-02):

  GET /api/playground/orchestrate?email=<judge-email>  (text/event-stream)

The former memory-loop A/B experiment (GET /api/playground/run), which staged
drift_patterns base rates and read drift_analyzer's severity_calibration, was
removed in P1.9 together with the drift_patterns pattern library:
leave-one-out validation showed that frequency-based severity correction made
errors worse and precedent retrieval gave no gain.

Run locally (WSL):
    uv run uvicorn apps.playground.server:app --port 8799     (from the repo root)
    # then: curl -N "http://127.0.0.1:8799/api/playground/orchestrate?email=you@example.com"
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, AsyncIterator

# Repo root on sys.path so `apps.playground...` / `ingestion...` resolve the same
# way the dispatcher reaches `apps.bff` (namespace packages, no __init__.py). See
# apps/dispatcher/main.py for the same pattern.
_REPO = Path(__file__).resolve().parents[2]
_AGENTS = _REPO / "agents"
for _p in (_REPO, _AGENTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_AGENTS / ".env")

import os  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="claimdrift-playground")

# The Playground is called DIRECTLY from the browser (unlike the dispatcher,
# which only Pub/Sub hits server-to-server), so it needs CORS. Allow the
# frontend's Cloud Run origin (PLAYGROUND_ALLOWED_ORIGINS, comma-separated) plus
# localhost for dev. Default to "*" if unset so a fresh deploy isn't dead on
# arrival; tighten via env in production.
_origins_env = os.getenv("PLAYGROUND_ALLOWED_ORIGINS", "").strip()
_allowed_origins = [o.strip() for o in _origins_env.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# --- SSE helpers ------------------------------------------------------------

def _sse(event: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _with_keepalive(
    gen: AsyncIterator[str], interval: float = 0.5
) -> AsyncIterator[str]:
    """Wrap an SSE generator so that whenever it goes silent for `interval`
    seconds, a `: ka` comment frame is emitted to keep the transport flushing.

    WHY (root-caused 2026-06-08): Google Frontend buffers the event-stream and
    holds an emitted frame in its send buffer until later output pushes past a
    size threshold. Both experiments have long silent gaps — experiment 1 (A/B)
    goes quiet for ~15s during each state's `_run_drift_analyzer()` live call
    (state.analyzing yielded, then nothing until state.done), and experiment 2
    most visibly between `pipeline.ready` and the supervisor's first inference
    chunk — during which the already-yielded frames sit undelivered for tens of
    seconds on the client even though the server emitted them in <1ms. That is
    why the A/B log appeared to "dump 7 lines at once" the instant the score
    showed: the early frames were stuck in the GFE buffer and only flushed when
    state.done pushed past the size threshold. `Cache-Control: no-transform` did
    not help (GFE ignores it for streaming). Periodic comment frames are the
    reliable fix: continuous small writes keep the buffer flushing so every real
    frame arrives promptly. `:`-prefixed comments are ignored by the EventSource
    spec, so the UI never sees them. Wrapping at the response layer covers EVERY
    silent gap in one place; both endpoints now use it."""
    it = gen.__aiter__()
    while True:
        nxt = asyncio.ensure_future(it.__anext__())
        while True:
            done, _ = await asyncio.wait({nxt}, timeout=interval)
            if done:
                break
            yield ": ka\n\n"
        try:
            yield nxt.result()
        except StopAsyncIteration:
            return


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# SSE response headers. X-Accel-Buffering: no is REQUIRED on Cloud Run: without
# it the platform proxy buffers the event-stream and releases nothing until the
# response ends, so a long live run shows a blank UI for its whole duration even
# though frames are being yielded (observed 2026-06-08 on the orchestration
# endpoint — stream connected, 200 OK, but no node lit up until completion).
# no-transform tells intermediaries (incl. Google Frontend) NOT to transform or
# buffer the body — added 2026-06-08 to test whether it makes GFE flush the lone
# post-await `pipeline.ready` frame immediately. Root-caused that day: server
# yields ready in <1ms but the client received it 17-282s later because the
# isolated frame after the warm-up await sat in the GFE/HTTP2 send buffer until
# later output pushed past the threshold. X-Accel-Buffering: no alone did NOT
# cover this (it stops whole-stream buffering, not per-frame hold-back).
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "Access-Control-Allow-Origin": "*",
    "X-Accel-Buffering": "no",
}


# --- Supervisor orchestration experiment -------------------------------------
# The full-supervisor live pipeline + custom-email + precise-id teardown lives
# in orchestration.py.
from apps.playground.orchestration import orchestrate as _orchestrate_supervisor  # noqa: E402


@app.get("/api/playground/orchestrate")
async def playground_orchestrate(email: str = "") -> StreamingResponse:
    """Run the full supervisor pipeline live, light up nodes, send the
    judge (?email=) their drift alert, then tear down this run's writes by
    captured id. See orchestration.py for the teardown-isolation rationale."""
    return StreamingResponse(
        _with_keepalive(_orchestrate_supervisor(email.strip())),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
