"""Unit tests for the supervisor hardening layer (C2 / D5).

Pure, deterministic, zero-token: a fake call-factory yields canned events and
the extractor is injected, so nothing touches Vertex / the network / an LLM.
`sleep` is injected as a recorder so backoff is asserted without real waiting.

Covers the three guarantees (hardening.run_sub_agent_guarded):
  - retry: transient failure on early attempts, success later.
  - timeout: a hung stream is bounded and counts as a retryable failure.
  - schema gate: a truthy-but-wrong output is rejected (the T1 bug class).
  - exhaustion: SubAgentError after max_attempts, carrying context.
  - backoff: the exact delay sequence fed to sleep.
  - require_output=False: tolerates a missing output without raising.

Run (from agents/):
    uv run --with pytest python -m pytest supervisor_agent/tests/test_hardening.py -q
    # or stdlib:
    uv run python -m unittest supervisor_agent.tests.test_hardening
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from supervisor_agent.hardening import (  # noqa: E402
    RetryPolicy,
    SubAgentError,
    run_sub_agent_guarded,
)
from supervisor_agent.schemas import DRIFT_ANALYZER_OUTPUT  # noqa: E402


def _factory_from_attempts(attempts):
    """Build a call_factory whose successive invocations behave per `attempts`.

    Each element of `attempts` is either:
      - a list of events (the stream yields them and completes), or
      - an Exception instance (the stream raises it), or
      - the string "hang" (the stream never completes — for timeout tests).
    A counter advances one element per factory call (i.e. per attempt).
    """
    state = {"i": 0}

    def factory():
        idx = state["i"]
        state["i"] += 1
        behavior = attempts[idx]

        async def gen():
            if behavior == "hang":
                await asyncio.Event().wait()  # never set -> hangs
                return
            if isinstance(behavior, Exception):
                raise behavior
            for ev in behavior:
                yield ev

        return gen()

    return factory, state


# A trivial extractor: events are already dicts; return the last one (or None).
def _last_dict_extractor(events):
    for ev in reversed(events):
        if isinstance(ev, dict):
            return ev
    return None


_GOOD_DRIFT = {"drift_summary": "x", "claim_diffs": [], "event_id": None}

_FAST = RetryPolicy(max_attempts=3, timeout_s=0.5, base_backoff_s=0.01)


class HardeningTest(unittest.IsolatedAsyncioTestCase):
    async def _sleep_recorder(self):
        recorded = []

        async def _sleep(d):
            recorded.append(d)

        return recorded, _sleep

    async def test_happy_path_one_attempt(self):
        factory, _ = _factory_from_attempts([[_GOOD_DRIFT]])
        res = await run_sub_agent_guarded(
            "drift_analyzer", factory, _last_dict_extractor, DRIFT_ANALYZER_OUTPUT,
            policy=_FAST,
        )
        self.assertEqual(res.attempts, 1)
        self.assertEqual(res.output, _GOOD_DRIFT)
        self.assertEqual(res.events, [_GOOD_DRIFT])

    async def test_retry_then_success(self):
        recorded, sleep = await self._sleep_recorder()
        factory, _ = _factory_from_attempts([
            RuntimeError("transient 503"),   # attempt 1 fails
            [_GOOD_DRIFT],                    # attempt 2 succeeds
        ])
        res = await run_sub_agent_guarded(
            "drift_analyzer", factory, _last_dict_extractor, DRIFT_ANALYZER_OUTPUT,
            policy=_FAST, sleep=sleep,
        )
        self.assertEqual(res.attempts, 2)
        self.assertEqual(res.output, _GOOD_DRIFT)
        # one backoff happened, before the 2nd attempt
        self.assertEqual(recorded, [_FAST.backoff_for(0)])

    async def test_schema_gate_rejects_truthy_but_wrong(self):
        # A truthy object missing required §3.2.2 fields (the T1 bug class):
        # an MCP wrapper. Should be treated as failure and retried; exhausts.
        bad = {"content": [{"type": "text", "text": "..."}], "isError": False}
        factory, _ = _factory_from_attempts([[bad], [bad], [bad]])
        with self.assertRaises(SubAgentError) as ctx:
            await run_sub_agent_guarded(
                "drift_analyzer", factory, _last_dict_extractor, DRIFT_ANALYZER_OUTPUT,
                policy=_FAST, sleep=lambda d: asyncio.sleep(0),
            )
        self.assertIn("schema validation failed", ctx.exception.reason)
        self.assertEqual(ctx.exception.attempts, 3)

    async def test_schema_gate_then_valid(self):
        bad = {"drift_summary": 123}  # wrong type for drift_summary, no claim_diffs
        factory, _ = _factory_from_attempts([[bad], [_GOOD_DRIFT]])
        res = await run_sub_agent_guarded(
            "drift_analyzer", factory, _last_dict_extractor, DRIFT_ANALYZER_OUTPUT,
            policy=_FAST, sleep=lambda d: asyncio.sleep(0),
        )
        self.assertEqual(res.attempts, 2)
        self.assertEqual(res.output, _GOOD_DRIFT)

    async def test_timeout_is_bounded_and_retryable(self):
        # attempt 1 hangs (timeout), attempt 2 succeeds.
        factory, _ = _factory_from_attempts(["hang", [_GOOD_DRIFT]])
        res = await run_sub_agent_guarded(
            "drift_analyzer", factory, _last_dict_extractor, DRIFT_ANALYZER_OUTPUT,
            policy=RetryPolicy(max_attempts=2, timeout_s=0.05, base_backoff_s=0.0),
            sleep=lambda d: asyncio.sleep(0),
        )
        self.assertEqual(res.attempts, 2)

    async def test_exhaustion_raises_with_context(self):
        err = RuntimeError("always down")
        factory, _ = _factory_from_attempts([err, err, err])
        with self.assertRaises(SubAgentError) as ctx:
            await run_sub_agent_guarded(
                "drift_analyzer", factory, _last_dict_extractor, DRIFT_ANALYZER_OUTPUT,
                policy=_FAST, sleep=lambda d: asyncio.sleep(0),
            )
        self.assertEqual(ctx.exception.agent_name, "drift_analyzer")
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertIn("always down", ctx.exception.reason)

    async def test_backoff_sequence_exact(self):
        recorded, sleep = await self._sleep_recorder()
        err = RuntimeError("down")
        factory, _ = _factory_from_attempts([err, err, err, err])
        policy = RetryPolicy(max_attempts=4, timeout_s=0.5, base_backoff_s=1.0,
                             backoff_multiplier=2.0, max_backoff_s=30.0)
        with self.assertRaises(SubAgentError):
            await run_sub_agent_guarded(
                "drift_analyzer", factory, _last_dict_extractor, DRIFT_ANALYZER_OUTPUT,
                policy=policy, sleep=sleep,
            )
        # 3 backoffs between 4 attempts: 1, 2, 4 (no sleep after the last)
        self.assertEqual(recorded, [1.0, 2.0, 4.0])

    async def test_backoff_capped(self):
        policy = RetryPolicy(base_backoff_s=10.0, backoff_multiplier=10.0, max_backoff_s=15.0)
        self.assertEqual(policy.backoff_for(0), 10.0)
        self.assertEqual(policy.backoff_for(1), 15.0)  # 100 capped to 15
        self.assertEqual(policy.backoff_for(5), 15.0)

    async def test_require_output_false_tolerates_missing(self):
        # No dict in the events -> extractor returns None. With require_output
        # False this is NOT a failure; output stays None, events returned.
        factory, _ = _factory_from_attempts([["just-a-string-event"]])
        res = await run_sub_agent_guarded(
            "memory_synthesizer", factory,
            extractor=_last_dict_extractor, schema=None,
            policy=_FAST, require_output=False,
        )
        self.assertEqual(res.attempts, 1)
        self.assertIsNone(res.output)
        self.assertEqual(res.events, ["just-a-string-event"])

    async def test_no_schema_skips_validation(self):
        anything = {"whatever": 1}
        factory, _ = _factory_from_attempts([[anything]])
        res = await run_sub_agent_guarded(
            "x", factory, _last_dict_extractor, schema=None, policy=_FAST,
        )
        self.assertEqual(res.output, anything)


if __name__ == "__main__":
    unittest.main()
