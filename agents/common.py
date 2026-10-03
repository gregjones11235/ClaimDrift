"""ADK wrapper shared by the five ClaimDrift agents deployed to Vertex AI Agent Engine (部署方案 A1).

Each agent is a custom BaseAgent around one handler of claimdrift.agent_handlers -- the same function a local run calls
in-process, so prompts, models and code paths are identical in both places. The request is the JSON payload as the
user message; the agent streams one ADK event per progress message and one with the result, each carrying a JSON text
part in the ClaimDrift protocol (see claimdrift/engines.py):
  {"cd": "progress", "data": {...}} ... {"cd": "result", "data": {...}}   or   {"cd": "error", "error": "..."}
Errors are reported in-band instead of raised: a raised exception reaches the caller as an opaque HTTP 500.

Sessions are kept in memory (one throw-away session per request): every agent is stateless and Agent Engine's managed
Sessions / Memory Bank are not used (they are billed separately).
"""
from __future__ import annotations

import asyncio
import json
import logging
import traceback
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.genai import types
from typing_extensions import override

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agents")


class FunctionAgent(BaseAgent):
    """Runs claimdrift.agent_handlers.HANDLERS[handler_name] in a worker thread and streams its progress."""

    handler_name: str = ""

    def _event(self, ctx: InvocationContext, msg: dict) -> Event:
        return Event(author=self.name, invocation_id=ctx.invocation_id,
                     content=types.Content(role="model", parts=[types.Part(text=json.dumps(msg, ensure_ascii=False, default=str))]))

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        from claimdrift.agent_handlers import HANDLERS
        try:
            text = "".join(p.text or "" for p in (ctx.user_content.parts if ctx.user_content else []) if getattr(p, "text", None))
            payload = json.loads(text)
        except Exception as e:  # noqa: BLE001
            yield self._event(ctx, {"cd": "error", "error": f"request must be one JSON object: {type(e).__name__}: {e}"})
            return
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def emit(data: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ("progress", data))

        def work() -> None:
            try:
                result = HANDLERS[self.handler_name](payload, emit)
                loop.call_soon_threadsafe(queue.put_nowait, ("result", result))
            except Exception as e:  # noqa: BLE001
                log.exception("%s failed", self.handler_name)
                loop.call_soon_threadsafe(queue.put_nowait, ("error", f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"))

        task = loop.run_in_executor(None, work)
        while True:
            kind, data = await queue.get()
            if kind == "progress":
                yield self._event(ctx, {"cd": "progress", "data": data})
                continue
            yield self._event(ctx, {"cd": "result", "data": data} if kind == "result" else {"cd": "error", "error": data})
            break
        await task


def in_memory_sessions():
    from google.adk.sessions import InMemorySessionService
    return InMemorySessionService()


DESCRIPTIONS = {
    "claim_extractor": "ClaimDrift claim_extractor: findings and method/definition statements of one paper version (flash).",
    "drift_analyzer": "ClaimDrift drift_analyzer: compares the preprint v1 and published claim lists, full-text and abstract-level severity (pro).",
    "citation_finder": "ClaimDrift citation_finder: citation orchestra (orchestrator + workers, ReAct over MCP tools), one step per call (pro).",
    "notifier": "ClaimDrift notifier: drafts the alert for one citing paper that relies on a revised value (flash). Drafts only.",
    "supervisor": "ClaimDrift supervisor: fixed orchestration of the other four agents (claim_extractor x2 -> drift_analyzer -> "
                  "citation_finder -> notifier xN) plus quote verification through MCP. No model of its own.",
}


def build(name: str) -> FunctionAgent:
    return FunctionAgent(name=name, description=DESCRIPTIONS[name], handler_name=name)


def app(name: str):
    from vertexai.agent_engines import AdkApp
    return AdkApp(agent=build(name), session_service_builder=in_memory_sessions)
