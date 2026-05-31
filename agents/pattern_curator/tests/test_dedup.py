"""Dedup orchestration tests (C3 / D3) — FakeStore + fake judge, zero tokens."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from pattern_curator import dedup  # noqa: E402
from pattern_curator.tests.fakes import FakeStore  # noqa: E402

NOW = "2026-05-31T00:00:00Z"


def _pat(pid, support, events, tags=("covid-19",), desc="COVID RCT effect-size reduction"):
    # Scan results carry _seq_no/_primary_term (es_ops sets seq_no_primary_term:
    # true), so the survivor-from-scan path has the tokens an OCC write needs.
    return {
        "pattern_id": pid, "pattern_type": "effect_size_reduction",
        "pattern_description": desc, "domain_tags": list(tags),
        "source_event_ids": list(events), "support_count": support,
        "created_at": NOW, "last_updated_at": NOW,
        "_seq_no": 0, "_primary_term": 1,
    }


def merge_yes(survivor_id):
    return lambda payload: {
        "same_phenomenon": True, "confidence": "high",
        "merge_into_pattern_id": survivor_id,
        "merged_description": "COVID clinical RCT preprints often show large "
                              "effect-size reductions at publication with hedging.",
        "rationale": "same phenomenon",
    }


def merge_no():
    return lambda payload: {
        "same_phenomenon": False, "confidence": "low",
        "merge_into_pattern_id": None, "merged_description": None,
        "rationale": "not the same",
    }


class DedupTest(unittest.TestCase):
    def test_applies_merge_and_unions_evidence(self):
        a = _pat("a", 12, ["e1", "e2"])
        b = _pat("b", 8, ["e3"])
        store = FakeStore([a, b], recall_map={"a": ["b"]})
        report = dedup.run_dedup(store, [a, b], NOW, apply=True, raw_judge=merge_yes("a"))
        self.assertEqual(len(report.merges_applied), 1)
        m = report.merges_applied[0]
        self.assertEqual(m.survivor_id, "a")
        self.assertEqual(m.loser_id, "b")
        self.assertEqual(m.new_support_count, 3)           # union e1,e2,e3
        # survivor updated, loser gone
        self.assertEqual(store.patterns["a"]["support_count"], 3)
        self.assertNotIn("b", store.patterns)
        self.assertIn("b", store.deleted)

    def test_decline_keeps_both(self):
        a = _pat("a", 12, ["e1"])
        b = _pat("b", 8, ["e2"])
        store = FakeStore([a, b], recall_map={"a": ["b"]})
        report = dedup.run_dedup(store, [a, b], NOW, apply=True, raw_judge=merge_no())
        self.assertEqual(report.declined, 1)
        self.assertEqual(len(report.merges_applied), 0)
        self.assertIn("a", store.patterns)
        self.assertIn("b", store.patterns)

    def test_dry_run_proposes_without_writing(self):
        a = _pat("a", 12, ["e1"])
        b = _pat("b", 8, ["e2"])
        store = FakeStore([a, b], recall_map={"a": ["b"]})
        report = dedup.run_dedup(store, [a, b], NOW, apply=False, raw_judge=merge_yes("a"))
        self.assertEqual(len(report.merges_proposed_only), 1)
        self.assertEqual(len(report.merges_applied), 0)
        self.assertIn("b", store.patterns)  # nothing deleted

    def test_loser_not_repaired_twice(self):
        # a merges b; b should not then be scanned as its own row
        a = _pat("a", 12, ["e1"])
        b = _pat("b", 8, ["e2"])
        store = FakeStore([a, b], recall_map={"a": ["b"], "b": ["a"]})
        report = dedup.run_dedup(store, [a, b], NOW, apply=True, raw_judge=merge_yes("a"))
        self.assertEqual(len(report.merges_applied), 1)  # only one merge, not two

    def test_occ_conflict_skips_merge(self):
        a = _pat("a", 12, ["e1"])
        b = _pat("b", 8, ["e2"])
        store = FakeStore([a, b], recall_map={"a": ["b"]})
        # Simulate a concurrent append to the survivor: bump its stored seq_no
        # so the merge's OCC write (carrying the old token) 409s.
        store.patterns["a"]["_seq_no"] = 5
        report = dedup.run_dedup(store, [a, b], NOW, apply=True, raw_judge=merge_yes("a"))
        self.assertEqual(len(report.merges_applied), 0)
        self.assertEqual(len(report.conflicts), 1)
        # both rows survive the conflict
        self.assertIn("a", store.patterns)
        self.assertIn("b", store.patterns)

    def test_pair_judged_at_most_once(self):
        # a recalls b AND b recalls a, but they DECLINE — the pair must be judged
        # exactly once, not twice (the timeout-root-cause fix). A counting judge
        # records how many times it was asked.
        a = _pat("a", 12, ["e1"])
        b = _pat("b", 8, ["e2"])
        calls = {"n": 0}

        def counting_no(payload):
            calls["n"] += 1
            return {"same_phenomenon": False, "confidence": "low",
                    "merge_into_pattern_id": None, "merged_description": None,
                    "rationale": "no"}

        store = FakeStore([a, b], recall_map={"a": ["b"], "b": ["a"]})
        report = dedup.run_dedup(store, [a, b], NOW, apply=True, raw_judge=counting_no)
        self.assertEqual(report.pairs_judged, 1)
        self.assertEqual(calls["n"], 1)  # NOT 2 — pair-dedup held

    def test_max_judgments_caps_and_flags(self):
        # 3 mutually-recalling patterns that all decline -> 3 possible pairs
        # (a-b, a-c, b-c). Cap at 2 -> only 2 judged, judgments_capped True.
        a = _pat("a", 3, ["e1"])
        b = _pat("b", 2, ["e2"])
        c = _pat("c", 1, ["e4"])
        store = FakeStore([a, b, c],
                          recall_map={"a": ["b", "c"], "b": ["a", "c"], "c": ["a", "b"]})
        report = dedup.run_dedup(store, [a, b, c], NOW, apply=True,
                                 raw_judge=merge_no(), max_judgments=2)
        self.assertEqual(report.pairs_judged, 2)
        self.assertTrue(report.judgments_capped)

    def test_unbounded_when_cap_none(self):
        a = _pat("a", 3, ["e1"])
        b = _pat("b", 2, ["e2"])
        c = _pat("c", 1, ["e4"])
        store = FakeStore([a, b, c],
                          recall_map={"a": ["b", "c"], "b": ["a", "c"], "c": ["a", "b"]})
        report = dedup.run_dedup(store, [a, b, c], NOW, apply=True,
                                 raw_judge=merge_no(), max_judgments=None)
        self.assertEqual(report.pairs_judged, 3)   # all 3 unordered pairs
        self.assertFalse(report.judgments_capped)


if __name__ == "__main__":
    unittest.main()
