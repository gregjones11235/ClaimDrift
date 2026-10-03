"""MCP client (action-layer side). Connects to the tool service -- over streamable HTTP when CLAIMDRIFT_MCP_URL (or `url`)
is set, otherwise by starting `python -m claimdrift.mcp_server` over stdio -- discovers its tools and offers a
synchronous interface usable from plain threads (the citation workers run in a thread pool).

  client = McpClient()                                  # one server process, one session
  client.call("verify_quote", {...}) -> dict
  view = client.bind(allowed={"get_citation_sentences", "search_in_work"}, target_id="...")
  view.schemas                                          # function declarations from the server's tool list, with the
                                                        # bound parameters (paper_id / target_id) hidden from the model
  view.call(name, args) -> JSON string                  # same contract as a local tool box

The session lives inside ONE long-lived task (anyio requires enter/exit in the same task); calls from other threads are
marshalled onto the client's event loop, so one client can be shared by parallel workers.

Deployed, the tool service is a private Cloud Run service (部署方案 S1/S2): every HTTP request to a *.run.app URL carries
a Google ID token for that service, fetched from the metadata server (Cloud Run, Agent Engine) or, on a developer
machine, from `gcloud auth print-identity-token`; tokens are refreshed before they expire (about one hour).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.parse

from . import config

log = logging.getLogger("claimdrift.mcp_client")


def _jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except Exception:  # noqa: BLE001
        return time.time() + 600


def needs_id_token(url: str) -> bool:
    mode = os.environ.get("CLAIMDRIFT_MCP_AUTH", "auto")
    if mode in ("none", "id_token"):
        return mode == "id_token"
    return urllib.parse.urlparse(url).hostname.endswith(".run.app")


class IdTokenSource:
    """Google ID token for a Cloud Run audience, cached until 5 minutes before expiry."""

    def __init__(self, audience: str):
        self.audience = audience
        self._token, self._exp = "", 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> str:
        try:
            import google.auth.transport.requests
            import google.oauth2.id_token
            return google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), self.audience)
        except Exception as e:  # noqa: BLE001 -- user credentials cannot mint audience tokens; use the gcloud CLI
            log.info("metadata/service-account ID token unavailable (%s); falling back to gcloud", type(e).__name__)
            return subprocess.run(["gcloud", "auth", "print-identity-token"], check=True, capture_output=True,
                                  text=True).stdout.strip()

    def token(self) -> str:
        with self._lock:
            if time.time() > self._exp - 300:
                self._token = self._fetch()
                self._exp = _jwt_exp(self._token)
            return self._token


def _httpx_auth(url: str):
    import httpx

    class _Auth(httpx.Auth):
        def __init__(self, source: IdTokenSource):
            self.source = source

        def auth_flow(self, request):
            request.headers["Authorization"] = f"Bearer {self.source.token()}"
            yield request

    p = urllib.parse.urlparse(url)
    return _Auth(IdTokenSource(f"{p.scheme}://{p.netloc}"))


class McpClient:
    def __init__(self, timeout: float = 600.0, url: str | None = None):
        from mcp import ClientSession, StdioServerParameters  # lazy: only the MCP path needs the SDK
        from mcp.client.stdio import stdio_client
        self._ClientSession, self._Params, self._stdio = ClientSession, StdioServerParameters, stdio_client
        self.url = url or config.MCP_URL
        self.timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._run(self._connect())
        self.tools = {t.name: t for t in self._run(self.session.list_tools()).tools}

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=self.timeout)

    async def _connect(self):
        ready = asyncio.Event()
        self._closing = asyncio.Event()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(config.ROOT) + os.pathsep + env.get("PYTHONPATH", "")

        async def serve(read, write):
            async with self._ClientSession(read, write) as session:
                await session.initialize()
                self.session = session
                ready.set()
                await self._closing.wait()

        async def lifetime():
            if self.url:
                from mcp.client.streamable_http import streamablehttp_client
                auth = _httpx_auth(self.url) if needs_id_token(self.url) else None
                # read timeout = the longest tool call (a pre-screen downloads hundreds of full texts)
                async with streamablehttp_client(self.url, timeout=60, sse_read_timeout=self.timeout, auth=auth) as (read, write, _):
                    await serve(read, write)
            else:
                params = self._Params(command=sys.executable, args=["-m", "claimdrift.mcp_server"], env=env, cwd=str(config.ROOT))
                async with self._stdio(params) as (read, write):
                    await serve(read, write)

        self._lifetime = asyncio.ensure_future(lifetime())
        await ready.wait()

    def call(self, name: str, args: dict):
        r = self._run(self.session.call_tool(name, args))
        if r.isError:
            return {"error": " ".join(getattr(c, "text", "") for c in r.content)[:500]}
        if r.structuredContent is not None:
            sc = r.structuredContent
            return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        txt = " ".join(getattr(c, "text", "") for c in r.content)
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            return {"text": txt}

    def bind(self, allowed: set[str], **bound) -> "BoundTools":
        return BoundTools(self, allowed, bound)

    def close(self):
        async def _shutdown():
            self._closing.set()
            await self._lifetime
        try:
            self._run(_shutdown())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=10)
            if not self._loop.is_running():
                self._loop.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


_shared: McpClient | None = None
_shared_lock = threading.Lock()


def shared() -> McpClient:
    """One client per process (the agents' handlers and the BFF share it; the client is thread-safe)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = McpClient()
        return _shared


class BoundTools:
    """A subset of the server's tools with some parameters fixed by the program (never chosen by the model)."""

    def __init__(self, client: McpClient, allowed: set[str], bound: dict):
        self.client, self.allowed, self.bound = client, allowed, bound
        self.log: list[dict] = []
        self.schemas = []
        self.accepts: dict[str, set[str]] = {}
        for name in sorted(allowed):
            t = client.tools.get(name)
            if t is None:
                raise KeyError(f"MCP server has no tool {name!r}")
            params = json.loads(json.dumps(t.inputSchema))
            self.accepts[name] = set(params.get("properties", {}))
            for k in bound:
                params.get("properties", {}).pop(k, None)
            params["required"] = [r for r in params.get("required", []) if r not in bound]
            for p in params.get("properties", {}).values():
                p.pop("title", None)
            params.pop("title", None)
            self.schemas.append({"type": "function", "function": {"name": name, "description": t.description or "", "parameters": params}})

    def invoke(self, name: str, args: dict):
        # bound values win over anything the model sent; only parameters the tool declares are passed
        bound = {k: v for k, v in self.bound.items() if k in self.accepts.get(name, self.bound)}
        return self.client.call(name, {**(args or {}), **bound})

    def call(self, name: str, args: dict) -> str:
        if name not in self.allowed:
            out = json.dumps({"error": f"tool {name} not available"})
        else:
            try:
                out = json.dumps(self.invoke(name, args), ensure_ascii=False)
            except Exception as e:  # noqa: BLE001
                out = json.dumps({"error": f"{type(e).__name__}: {e}"})
        self.log.append({"tool": name, "args": args, "result_chars": len(out), "result_digest": out[:200], "via": "mcp"})
        return out
