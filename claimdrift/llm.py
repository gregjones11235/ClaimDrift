"""Gemini chat backend (AI Studio API key). Same message shape and call semantics as the prototype
(data/cases/experiments/llm.py) so production results stay comparable with the experiments.

  backend.chat(messages, tools=None) -> {"content": str, "tool_calls": [{"name", "args"}], "assistant_message": dict}
Messages use the OpenAI shape: {"role": system|user|assistant|tool, "content": str, ...}.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from . import config

log = logging.getLogger("claimdrift.llm")


class Gemini:
    def __init__(self, model: str = config.MODEL_PRO, temperature: float = 0.0):
        from google import genai  # lazy: importing the package must not require the SDK
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set (expected in .env)")
        self.client = genai.Client(api_key=key, vertexai=False)
        self.model, self.temperature = model, temperature
        self.calls, self.total_secs = 0, 0.0
        self.prompt_tokens = self.output_tokens = self.cached_tokens = self.last_prompt_tokens = 0
        self._lock = threading.Lock()

    def usage(self) -> dict:
        return {"model": self.model, "calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "output_tokens": self.output_tokens, "secs": round(self.total_secs, 1)}

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        from google.genai import types
        sys_txt = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = []
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=m["content"])]))
            elif m["role"] == "assistant" and m.get("_gemini_content") is not None:
                # Gemini 3 requires the thought_signature on functionCall parts to be echoed back verbatim
                contents.append(m["_gemini_content"])
            elif m["role"] == "assistant":
                parts = [types.Part(text=m["content"])] if m.get("content") else []
                for c in m.get("tool_calls", []) or []:
                    parts.append(types.Part(function_call=types.FunctionCall(name=c["function"]["name"], args=c["function"]["arguments"])))
                contents.append(types.Content(role="model", parts=parts))
            elif m["role"] == "tool":
                contents.append(types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
                    name=m["tool_name"], response={"result": m["content"]}))]))
        cfg = types.GenerateContentConfig(system_instruction=sys_txt or None, temperature=self.temperature)
        if tools:
            decls = [types.FunctionDeclaration(name=t["function"]["name"], description=t["function"]["description"],
                                               parameters=t["function"]["parameters"]) for t in tools]
            cfg.tools = [types.Tool(function_declarations=decls)]
            cfg.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        t = time.time()
        for attempt in range(6):  # 503 high demand / 429 rate limit are transient
            try:
                r = self.client.models.generate_content(model=self.model, contents=contents, config=cfg)
                break
            except Exception as e:  # noqa: BLE001
                code = getattr(e, "code", None) or getattr(e, "status_code", None)
                if code not in (429, 500, 503, 504) or attempt == 5:
                    raise
                wait = min(60, 5 * 2 ** attempt)
                log.warning("%s: HTTP %s (attempt %d), retrying in %ds", self.model, code, attempt + 1, wait)
                time.sleep(wait)
        log.info("%s call %.1fs", self.model, time.time() - t)
        um = getattr(r, "usage_metadata", None)
        with self._lock:
            self.calls += 1
            self.total_secs += time.time() - t
            if um is not None:
                self.prompt_tokens += um.prompt_token_count or 0
                self.output_tokens += (um.candidates_token_count or 0) + (getattr(um, "thoughts_token_count", 0) or 0)
                self.cached_tokens += getattr(um, "cached_content_token_count", 0) or 0
                self.last_prompt_tokens = um.prompt_token_count or 0
        calls, text = [], ""
        cand = (r.candidates or [None])[0]
        parts = (cand.content.parts if cand is not None and cand.content is not None else None) or []
        for p in parts:
            if p.function_call:
                calls.append({"name": p.function_call.name, "args": dict(p.function_call.args or {})})
            elif p.text:
                text += p.text
        am = {"role": "assistant", "content": text,
              "tool_calls": [{"function": {"name": c["name"], "arguments": c["args"]}} for c in calls],
              "_gemini_content": cand.content if cand is not None else None}
        return {"content": text, "tool_calls": calls, "assistant_message": am}

    def tool_result_message(self, name: str, content: str) -> dict:
        return {"role": "tool", "content": content, "tool_name": name}


def dump_messages(msgs: list[dict]) -> list[dict]:
    """JSON-safe copy of a conversation (stepwise citation runs persist it between steps). The Gemini Content of an
    assistant turn is kept as JSON so its thought_signature bytes survive the round trip (base64)."""
    out = []
    for m in msgs:
        m2 = {k: v for k, v in m.items() if k != "_gemini_content"}
        if m.get("_gemini_content") is not None:
            m2["_gemini_content_json"] = m["_gemini_content"].model_dump_json(exclude_none=True)
        out.append(m2)
    return out


def load_messages(data: list[dict]) -> list[dict]:
    from google.genai import types
    out = []
    for m in data:
        m2 = dict(m)
        if "_gemini_content_json" in m2:
            m2["_gemini_content"] = types.Content.model_validate_json(m2.pop("_gemini_content_json"))
        out.append(m2)
    return out


def pro() -> Gemini:
    return Gemini(config.MODEL_PRO)


def flash() -> Gemini:
    return Gemini(config.MODEL_FLASH)


def extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        return None
    chunk = text[a:b + 1]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))  # trailing commas
        except json.JSONDecodeError:
            return None


def tool_loop(backend, msgs: list[dict], schemas: list[dict], call, budget: int, max_turns: int,
              exhausted_msg: str = "Tool budget exhausted. Write the final JSON now.") -> str:
    """Generic bounded ReAct loop: run tool calls until the model answers with JSON or the budget is spent."""
    used, final = 0, ""
    for _ in range(max_turns):
        r = backend.chat(msgs, tools=schemas if used < budget else None)
        msgs.append(r["assistant_message"])
        if r["tool_calls"] and used < budget:
            # answer every call of the turn: Gemini rejects a turn whose function responses do not match its calls
            for c in r["tool_calls"]:
                used += 1
                msgs.append(backend.tool_result_message(c["name"], call(c["name"], c["args"] or {})))
            if used >= budget:
                msgs.append({"role": "user", "content": exhausted_msg})
            continue
        final = r["content"]
        if extract_json(final):
            break
        msgs.append({"role": "user", "content": "Output ONLY the JSON object now."})
    return final
