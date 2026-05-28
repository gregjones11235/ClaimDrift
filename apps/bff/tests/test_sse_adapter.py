"""Golden-stream test for sse_adapter.translate_stream.

The fixture is the same JSONL the dispatcher's T1 reference run produced; see
apps/dispatcher/tests/golden/stream_amblyopia_v2.jsonl. 13 raw ADK events ->
a known sequence of §6.1 envelopes. If this test breaks after editing the
translator, look at the diff carefully: the envelope shape is the frontend's
contract and changes to it must be intentional.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT))

from apps.bff.sse_adapter import (
    KNOWN_AGENT_IDS,
    TranslatorState,
    translate_adk_event,
    translate_stream,
)


GOLDEN = ROOT / "apps" / "dispatcher" / "tests" / "golden" / "stream_amblyopia_v2.jsonl"


def load_golden() -> list[dict]:
    text = GOLDEN.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class TestSseAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.events = load_golden()
        self.envelopes = translate_stream(self.events, drift_event_id="test-drift-001")

    def test_every_envelope_has_required_fields(self) -> None:
        required = {"event_type", "agent_id", "drift_event_id", "timestamp", "payload"}
        for env in self.envelopes:
            self.assertEqual(set(env.keys()), required, env)

    def test_event_types_are_in_contract(self) -> None:
        allowed = {
            "agent.started",
            "agent.tool_call",
            "agent.pattern_retrieved",
            "agent.completed",
            "agent.failed",
            "heartbeat",
        }
        for env in self.envelopes:
            self.assertIn(env["event_type"], allowed, env)

    def test_agent_ids_are_in_contract(self) -> None:
        for env in self.envelopes:
            if env["agent_id"] is None:
                continue
            self.assertIn(env["agent_id"], KNOWN_AGENT_IDS, env)

    def test_drift_event_id_propagated(self) -> None:
        for env in self.envelopes:
            self.assertEqual(env["drift_event_id"], "test-drift-001")

    def test_each_subagent_invocation_starts_exactly_once(self) -> None:
        starts_per_invocation: dict[tuple[str, str], int] = {}
        state = TranslatorState()
        for ev in self.events:
            envs = translate_adk_event(ev, state, drift_event_id=None)
            for e in envs:
                if e["event_type"] != "agent.started":
                    continue
                key = (e["agent_id"], ev.get("invocation_id") or "")
                starts_per_invocation[key] = starts_per_invocation.get(key, 0) + 1
        for key, count in starts_per_invocation.items():
            self.assertEqual(count, 1, f"agent.started fired {count}x for {key}")

    def test_two_parallel_claim_extractor_invocations_get_two_starts(self) -> None:
        starts = [
            e for e in self.envelopes
            if e["event_type"] == "agent.started" and e["agent_id"] == "claim_extractor"
        ]
        self.assertEqual(len(starts), 2, "claim_extractor runs preprint + published in parallel")

    def test_drift_analyzer_emits_tool_call_then_pattern_retrieved_then_completed(self) -> None:
        seq = [e["event_type"] for e in self.envelopes if e["agent_id"] == "drift_analyzer"]
        # started, tool_call, pattern_retrieved, completed
        self.assertEqual(
            seq,
            ["agent.started", "agent.tool_call", "agent.pattern_retrieved", "agent.completed"],
            seq,
        )

    def test_pattern_retrieved_payload_has_ids(self) -> None:
        prs = [e for e in self.envelopes if e["event_type"] == "agent.pattern_retrieved"]
        self.assertGreater(len(prs), 0, "memory loop produces at least one pattern_retrieved")
        for pr in prs:
            self.assertIn("pattern_ids", pr["payload"])
            self.assertIsInstance(pr["payload"]["pattern_ids"], list)
            self.assertIn("scores", pr["payload"])

    def test_tool_call_payload_has_name_and_args(self) -> None:
        tcs = [e for e in self.envelopes if e["event_type"] == "agent.tool_call"]
        self.assertGreater(len(tcs), 0)
        for tc in tcs:
            self.assertIn("tool_name", tc["payload"])
            self.assertIn("args", tc["payload"])
            self.assertIsInstance(tc["payload"]["args"], dict)

    def test_completed_only_when_text_parses(self) -> None:
        completed = [e for e in self.envelopes if e["event_type"] == "agent.completed"]
        # We expect exactly one completed per sub-agent invocation that produced
        # a parseable final JSON. From the golden stream that's:
        #   - claim_extractor x2 (preprint + published)
        #   - drift_analyzer x1
        #   - citation_finder x1
        #   - memory_synthesizer x1
        #   (notifier x0 — this pair had no affected citations)
        agent_counts: dict[str, int] = {}
        for e in completed:
            agent_counts[e["agent_id"]] = agent_counts.get(e["agent_id"], 0) + 1
        self.assertEqual(agent_counts, {
            "claim_extractor": 2,
            "drift_analyzer": 1,
            "citation_finder": 1,
            "memory_synthesizer": 1,
        })

    def test_timestamps_are_iso8601_z(self) -> None:
        import re
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        for env in self.envelopes:
            self.assertRegex(env["timestamp"], pattern)


if __name__ == "__main__":
    unittest.main()
