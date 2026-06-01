"""Per-phase degradation tests for the supervisor (C2 / D5).

Asserts the decided failure policy (staged degradation per contracts.md §3.x /
memory_loop_v2_design.md B.3):
  - core chain (claim_extractor / drift_analyzer / citation_finder) failure
    => fail-fast (the whole run raises);
  - notifier ×N: one citation's notifier failing => that email is SKIPPED
    (emitted as an error event) while the others still send and the run
    completes;
  - memory_synthesizer failure => NON-BLOCKING: an error event is emitted and
    the run finishes cleanly.

How this runs without Vertex / ADK plumbing
-------------------------------------------
`_run_async_impl` only reads ctx.user_content / ctx.user_id / ctx.invocation_id,
all duck-typed here with SimpleNamespace. We patch the module-level
`_call_sub_agent` with a fake that returns canned event streams keyed by agent
name, and shrink the retry policy so retries don't slow the test. No network,
no LLM, deterministic.
"""
from __future__ import annotations

import json
import sys
import types as _pytypes
import unittest
from pathlib import Path
from types import SimpleNamespace

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from supervisor_agent import agent as sup  # noqa: E402
from supervisor_agent.hardening import RetryPolicy  # noqa: E402


def _text_event(author: str, payload: dict):
    """Build an object shaped enough like an ADK Event for _extract_final_output
    (which reads .content.parts[*].text) and for our assertions (.author)."""
    part = SimpleNamespace(text=json.dumps(payload))
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(author=author, content=content, error_code=None, error_message=None)


# Canned good outputs per agent (minimal §3.x.2 shapes the schemas accept).
GOOD = {
    "claim_extractor": {"claims": [{"text": "c"}]},
    "drift_analyzer": {"drift_summary": "s", "claim_diffs": [], "event_id": None},
    "citation_finder": {
        "affected_citations": [
            {"citing_paper_doi": "10.1/a", "citing_paper_title": "A",
             "citing_paper_authors": [{"name": "X"}], "severity_tier": "central"},
            {"citing_paper_doi": "10.1/b", "citing_paper_title": "B",
             "citing_paper_authors": [{"name": "Y"}], "severity_tier": "peripheral"},
        ],
        "total_found": 2,
    },
    "notifier": {"affected_citation_id": "evt::10.1/a", "subject": "s", "body": "b"},
    "memory_synthesizer": {"action": "create_new"},
}

INPUT_ENVELOPE = {
    "preprint": {"doi": "10.1101/x", "version": "v1", "title": "t",
                 "abstract": "a", "conclusion": None},
    "published": {"doi": "10.1/pub", "version": "v1", "title": "t",
                  "abstract": "a", "conclusion": None},
}


def _make_ctx():
    part = SimpleNamespace(text=json.dumps(INPUT_ENVELOPE))
    return SimpleNamespace(
        user_content=SimpleNamespace(parts=[part]),
        user_id="test-user",
        invocation_id="inv-test",
    )


class _Harness:
    """Installs a fake _call_sub_agent on the module and restores it.

    `behaviors` maps agent_name -> callable(envelope, call_index) -> list[event]
    or raises. `call_index` lets notifier behave differently per citation.
    """

    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.calls = {}
        self._orig = None

    def __enter__(self):
        self._orig = sup._call_sub_agent

        async def fake_call_sub_agent(agent_name, envelope, user_id):
            idx = self.calls.get(agent_name, 0)
            self.calls[agent_name] = idx + 1
            fn = self.behaviors[agent_name]
            for ev in fn(envelope, idx):
                yield ev

        sup._call_sub_agent = fake_call_sub_agent
        return self

    def __exit__(self, *exc):
        sup._call_sub_agent = self._orig


def _default_good(name):
    return lambda env, idx: [_text_event(name, GOOD[name])]


async def _drive(behaviors):
    """Run _run_async_impl with patched internals; collect emitted events."""
    agent = sup.SupervisorAgent(name="supervisor_agent", description="t")
    # shrink retries so failure paths don't wait
    agent._retry_policy = RetryPolicy(max_attempts=2, timeout_s=1.0, base_backoff_s=0.0)
    out = []
    with _Harness(behaviors):
        async for ev in agent._run_async_impl(_make_ctx()):
            out.append(ev)
    return out


class DegradationTest(unittest.IsolatedAsyncioTestCase):
    def _all_good(self):
        return {n: _default_good(n) for n in GOOD}

    async def test_happy_path_completes(self):
        out = await _drive(self._all_good())
        authors = [getattr(e, "author", None) for e in out]
        # all five agents represented, plus the minted supervisor drift_event
        self.assertIn("memory_synthesizer", authors)
        self.assertIn("supervisor_agent", authors)  # minted drift_event event

    async def test_core_chain_drift_failure_is_fail_fast(self):
        b = self._all_good()
        b["drift_analyzer"] = lambda env, idx: (_ for _ in ()).throw(RuntimeError("drift down"))
        with self.assertRaises(RuntimeError) as ctx:
            await _drive(b)
        self.assertIn("drift_analyzer", str(ctx.exception))

    async def test_core_chain_extractor_failure_is_fail_fast(self):
        b = self._all_good()
        # both extractor calls fail
        b["claim_extractor"] = lambda env, idx: (_ for _ in ()).throw(RuntimeError("extract down"))
        with self.assertRaises(RuntimeError) as ctx:
            await _drive(b)
        self.assertIn("claim_extractor", str(ctx.exception))

    async def test_notifier_partial_failure_skips_one_completes_rest(self):
        b = self._all_good()

        # Key the behavior off the CITATION (envelope), not the global call
        # counter, so a citation that is meant to fail fails on every retry
        # attempt too (matching the real _call_sub_agent, which re-invokes with
        # the same envelope). Citation a always fails; citation b succeeds.
        def notifier_behavior(env, idx):
            if env.get("citing_paper_doi") == "10.1/a":
                raise RuntimeError("smtp boom")
            return [_text_event("notifier", GOOD["notifier"])]

        b["notifier"] = notifier_behavior
        out = await _drive(b)
        # run completed (reached memory_synthesizer)
        authors = [getattr(e, "author", None) for e in out]
        self.assertIn("memory_synthesizer", authors)
        # exactly one notifier error event was emitted for the skipped citation
        notifier_errors = [
            e for e in out
            if getattr(e, "author", None) == "notifier"
            and getattr(e, "error_code", None) == "sub_agent_failed"
        ]
        self.assertEqual(len(notifier_errors), 1)
        self.assertIn("notifier skipped", notifier_errors[0].error_message)

    async def test_memory_failure_is_non_blocking(self):
        b = self._all_good()
        b["memory_synthesizer"] = lambda env, idx: (_ for _ in ()).throw(RuntimeError("mem down"))
        # should NOT raise — memory is terminal/non-blocking
        out = await _drive(b)
        mem_errors = [
            e for e in out
            if getattr(e, "author", None) == "memory_synthesizer"
            and getattr(e, "error_code", None) == "sub_agent_failed"
        ]
        self.assertEqual(len(mem_errors), 1)
        self.assertIn("non-blocking", mem_errors[0].error_message)


if __name__ == "__main__":
    unittest.main()
