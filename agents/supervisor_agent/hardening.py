"""Deterministic hardening for supervisor sub-agent calls (C2 / D5).

This is PURE CODE — no LLM, no tokens, fully reproducible and unit-testable.
It implements the three guarantees from memory_loop_v2_design.md B.3 (the
supervisor-hardening / staged-degradation design, contracts.md §3.x) around
every remote sub-agent invocation:

  1. timeout     — a hung remote stream cannot block the supervisor forever.
  2. retry       — transient remote/network failures get N attempts with
                   exponential backoff + a deterministic-by-default jitter.
  3. schema gate — the extracted §3.x.2 output is validated against a minimal
                   schema (schemas.py) before the supervisor feeds it to the
                   next phase, so a truthy-but-wrong object (MCP wrapper,
                   partial JSON, hallucinated shape) is caught here instead of
                   poisoning downstream ES rows (the exact T1 2026-05-26 bug).

Why a separate module: keeps agent.py readable AND lets these guarantees be
tested in isolation by injecting a fake call-factory — no Vertex creds, no
network, no tokens. The supervisor stays "NOT an LLM agent" (agent.py:3); this
adds zero LLM logic.

Streaming semantics under retry
-------------------------------
Each attempt re-runs a FRESH sub-agent stream. We must not leak a failed
attempt's partial events into the dispatcher's stream (it would double-count or
show a phantom failed run). So `run_sub_agent_guarded` BUFFERS each attempt's
events and only returns the events of the attempt that succeeded. The caller
yields those buffered events into the supervisor stream after the call returns.
This trades true streaming for correctness-under-retry; at demo scale the
per-agent event count is tiny, so buffering is free. (The happy path is one
attempt, so there is no behavioral change when nothing fails.)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Awaitable, Callable

from jsonschema import Draft202012Validator

# A factory that produces a FRESH event stream for one attempt. We take a
# factory (not a single generator) because a generator can only be consumed
# once — retry needs a new stream each time.
CallFactory = Callable[[], AsyncGenerator[Any, None]]

# Extracts the §3.x.2 structured output dict from a list of events (or None).
# Injected so this module does not depend on agent.py (avoids a cycle and keeps
# it unit-testable). In production this is agent._extract_final_output.
Extractor = Callable[[list[Any]], "dict | None"]


class SubAgentError(RuntimeError):
    """Raised when a sub-agent call fails after all retry attempts.

    Carries enough context for the caller to decide fail-fast vs skip vs
    log-and-continue (the per-phase degradation policy in agent.py).
    """

    def __init__(self, agent_name: str, attempts: int, reason: str):
        self.agent_name = agent_name
        self.attempts = attempts
        self.reason = reason
        super().__init__(
            f"sub-agent '{agent_name}' failed after {attempts} attempt(s): {reason}"
        )


@dataclass(frozen=True)
class RetryPolicy:
    """Deterministic-by-default retry/timeout knobs.

    Defaults are tuned for the demo / low-QPS deployment: a couple of retries
    absorb a transient Agent Engine hiccup without turning a real outage into a
    multi-minute hang. The dispatcher already caps its own HTTP at ~60s
    (apps/dispatcher/main.py); per-attempt timeout here is the inner bound.

    `jitter` defaults to 0.0 so tests are fully reproducible. Production wiring
    may pass a small positive jitter fraction to de-correlate retries.
    """

    max_attempts: int = 3
    timeout_s: float = 90.0
    base_backoff_s: float = 1.0
    backoff_multiplier: float = 2.0
    max_backoff_s: float = 30.0
    jitter: float = 0.0  # fraction of the delay; 0 => deterministic

    def backoff_for(self, attempt_index: int) -> float:
        """Delay before the retry that follows attempt `attempt_index`
        (0-based). attempt 0 -> base, attempt 1 -> base*mult, capped."""
        delay = self.base_backoff_s * (self.backoff_multiplier ** attempt_index)
        return min(delay, self.max_backoff_s)


@dataclass
class GuardedResult:
    """Outcome of a guarded sub-agent call.

    events:  the buffered events of the SUCCESSFUL attempt — caller yields
             these into the supervisor stream.
    output:  the validated §3.x.2 output dict (always present on success).
    attempts: how many attempts it took (for observability/logging).
    """

    events: list[Any] = field(default_factory=list)
    output: dict | None = None
    attempts: int = 0


async def _drain_with_timeout(
    call_factory: CallFactory,
    timeout_s: float,
) -> list[Any]:
    """Consume one fresh stream fully, buffering events, under a wall-clock
    timeout for the whole attempt. Raises asyncio.TimeoutError on expiry,
    or whatever the stream raises on a remote/transport error."""

    async def _drain() -> list[Any]:
        buffered: list[Any] = []
        async for event in call_factory():
            buffered.append(event)
        return buffered

    return await asyncio.wait_for(_drain(), timeout=timeout_s)


async def run_sub_agent_guarded(
    agent_name: str,
    call_factory: CallFactory,
    extractor: Extractor,
    schema: dict | None,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    require_output: bool = True,
) -> GuardedResult:
    """Invoke a sub-agent with timeout + backoff-retry + schema validation.

    Args:
      agent_name: for error/log context.
      call_factory: returns a FRESH event stream each call (one per attempt).
      extractor: pulls the §3.x.2 output dict from the buffered events
        (agent._extract_final_output in production).
      schema: minimal JSON schema to validate the extracted output against
        (schemas.OUTPUT_SCHEMAS[agent_name]); None skips validation.
      policy: retry/timeout knobs; defaults to RetryPolicy().
      sleep: awaitable sleep, injectable so tests run instantly.
      require_output: if True, a missing/invalid output is a retryable failure
        and exhaustion raises SubAgentError. If False (rare), absence of a
        parseable output is tolerated and output stays None.

    Returns:
      GuardedResult with the successful attempt's events + validated output.

    Raises:
      SubAgentError: all attempts exhausted (timeout, transport error, or
        schema-invalid output). The caller applies the per-phase degradation
        policy (core chain fail-fast; notifier skip; memory log-and-continue).
    """
    policy = policy or RetryPolicy()
    validator = Draft202012Validator(schema) if schema is not None else None
    last_reason = "unknown"

    for attempt in range(policy.max_attempts):
        try:
            events = await _drain_with_timeout(call_factory, policy.timeout_s)
        except asyncio.TimeoutError:
            last_reason = f"timeout after {policy.timeout_s}s"
        except Exception as exc:  # remote/transport error from the stream
            last_reason = f"stream error: {type(exc).__name__}: {exc}"
        else:
            output = extractor(events)
            if output is None:
                if not require_output:
                    return GuardedResult(events=events, output=None, attempts=attempt + 1)
                last_reason = "no parseable §3.x.2 output in events"
            elif validator is not None:
                errors = sorted(validator.iter_errors(output), key=lambda e: e.path)
                if errors:
                    first = errors[0]
                    path = "/".join(str(p) for p in first.path) or "<root>"
                    last_reason = f"schema validation failed at {path}: {first.message}"
                else:
                    return GuardedResult(events=events, output=output, attempts=attempt + 1)
            else:
                return GuardedResult(events=events, output=output, attempts=attempt + 1)

        # failed this attempt; back off unless it was the last one
        if attempt < policy.max_attempts - 1:
            await sleep(policy.backoff_for(attempt))

    raise SubAgentError(agent_name, policy.max_attempts, last_reason)
