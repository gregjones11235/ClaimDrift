"""Calling the five agents: in-process (local) or on Vertex AI Agent Engine (CLAIMDRIFT_ENGINES set).

  engines.agents().call(name, payload, on_progress=None) -> result dict

Wire protocol of the deployed agents (agents/common.py): the request is the JSON payload as the user message; the
agent streams ADK events whose text part is one JSON object
  {"cd": "progress", "data": {...}}   any number
  {"cd": "result",   "data": {...}}   exactly one on success
  {"cd": "error",    "error": "..."}  on failure
A failed call is retried (agents have no side effects, so a retry is safe, 部署方案 A7).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

from . import config, serial

log = logging.getLogger("claimdrift.engines")
RESOURCE_RE = re.compile(r"projects/([^/]+)/locations/([^/]+)/reasoningEngines/(\d+)")
ATTEMPTS = 3


class EngineError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.ENGINES_SPEC)


@lru_cache(maxsize=1)
def resources() -> dict[str, str]:
    spec = (config.ENGINES_SPEC or "").strip()
    if not spec:
        return {}
    data = json.loads(spec if spec.startswith("{") else Path(spec).read_text(encoding="utf-8"))
    return {k: (v["resource_name"] if isinstance(v, dict) else v) for k, v in data.items()}


_engines: dict[str, object] = {}
_lock = threading.Lock()


def _engine(name: str):
    with _lock:
        if name not in _engines:
            res = resources().get(name)
            if not res:
                raise EngineError(f"no Agent Engine resource configured for {name!r} (CLAIMDRIFT_ENGINES)")
            m = RESOURCE_RE.fullmatch(res)
            if not m:
                raise EngineError(f"bad resource name for {name!r}: {res}")
            import vertexai
            from vertexai import agent_engines
            vertexai.init(project=m.group(1), location=m.group(2))
            _engines[name] = agent_engines.get(res)
        return _engines[name]


def messages(event: dict):
    """ClaimDrift protocol messages inside one streamed ADK event (a dict shaped like Event.model_dump())."""
    for part in ((event or {}).get("content") or {}).get("parts") or []:
        text = part.get("text") if isinstance(part, dict) else None
        if not text or not text.lstrip().startswith("{"):
            continue
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("cd") in ("progress", "result", "error"):
            yield msg


def call_remote(name: str, payload: dict, on_progress=None, user_id: str = "claimdrift") -> dict:
    message = json.dumps(payload, ensure_ascii=False)
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            result = None
            for ev in _engine(name).stream_query(message=message, user_id=user_id):
                for msg in messages(ev):
                    if msg["cd"] == "progress":
                        if on_progress is not None:
                            try:
                                on_progress(msg["data"])
                            except Exception:  # noqa: BLE001 -- display only
                                log.debug("progress callback failed", exc_info=True)
                    elif msg["cd"] == "result":
                        result = msg["data"]
                    else:
                        raise EngineError(f"{name}: {msg.get('error')}")
            if result is None:
                raise EngineError(f"{name}: stream ended without a result")
            return result
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < ATTEMPTS - 1:
                wait = 10 * (attempt + 1)
                log.warning("%s call failed (%s: %s); retry %d in %ds", name, type(e).__name__, str(e)[:300], attempt + 1, wait)
                time.sleep(wait)
    raise last  # type: ignore[misc]


class LocalAgents:
    """Every agent in-process (the same handlers the engines run)."""

    def call(self, name: str, payload: dict, on_progress=None) -> dict:
        from .agent_handlers import HANDLERS
        return HANDLERS[name](payload, on_progress or (lambda _ev: None))


class RemoteAgents:
    def call(self, name: str, payload: dict, on_progress=None) -> dict:
        return call_remote(name, payload, on_progress)


def agents():
    return RemoteAgents() if configured() else LocalAgents()


def analyze_remote(pair, force_route: str | None = None, on_progress=None) -> dict:
    """Layer ① on the supervisor engine (mode "analyze") -> pipeline.compute()'s {"event", "targets", "analysis"}.
    on_progress receives the supervisor's node frames ({"frame": "node.started", "data": {...}})."""
    return call_remote("supervisor", {"mode": "analyze", "pair": serial.pair_to_dict(pair), "force_route": force_route},
                       on_progress)["computed"]
