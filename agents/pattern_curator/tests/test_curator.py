"""End-to-end orchestration test for run_curator (C3 / D3) — FakeStore + fake
judge, zero tokens. Exercises scan -> hygiene -> eviction -> dedup -> watermark
in one pass, mirroring the E3 "duplicates + garbage" scenario in miniature."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from pattern_curator.curator import run_curator  # noqa: E402
from pattern_curator.tests.fakes import FakeStore  # noqa: E402

NOW = "2026-05-31T00:00:00Z"
E1 = "a1b2c3d4-e5f6-4789-abcd-1234567890ab"
E2 = "b2c3d4e5-f6a7-4890-bcde-2345678901bc"
E3 = "c3d4e5f6-a7b8-4901-8def-3456789012cd"  # variant nibble 8 (valid UUID v4)


def _pat(pid, support, events, **kw):
    # Scan results carry OCC tokens (es_ops sets seq_no_primary_term: true), so
    # a survivor pulled from the scan list has the tokens its merge write needs.
    base = {
        "pattern_id": pid, "pattern_type": "effect_size_reduction",
        "pattern_description": "COVID RCT effect-size reduction",
        "domain_tags": ["covid-19"], "source_event_ids": list(events),
        "support_count": support, "created_at": NOW, "last_updated_at": NOW,
        "_seq_no": 0, "_primary_term": 1,
    }
    base.update(kw)
    return base


def merge_yes(survivor_id):
    return lambda payload: {
        "same_phenomenon": True, "confidence": "high",
        "merge_into_pattern_id": survivor_id,
        "merged_description": "COVID clinical RCT preprints often show large "
                              "effect-size reductions at publication.",
        "rationale": "same phenomenon",
    }


class CuratorE2ETest(unittest.TestCase):
    def test_full_pass_hygiene_evict_dedup(self):
        # dup_a / dup_b: real duplicates to be merged.
        # garbage: a row whose only source_event_id is a hallucination ->
        #          hygiene strips it -> support 0 -> evicted.
        dup_a = _pat("dup_a", 2, [E1, E2])
        dup_b = _pat("dup_b", 1, [E3], domain_tags=["covid-19", "rct"])
        garbage = _pat("garbage", 1, ["drift_event_id_not_found_in_input"])
        store = FakeStore(
            [dup_a, dup_b, garbage],
            existing_event_ids={E1, E2, E3},
            recall_map={"dup_a": ["dup_b"]},
        )
        report = run_curator(
            store, apply=True, since=None, evict_min_support=1,
            raw_judge=merge_yes("dup_a"), now_iso=NOW,
        )

        # hygiene repaired the garbage row (stripped the hallucinated id)
        repaired_ids = {h["pattern_id"] for h in report.hygiene_repaired}
        self.assertIn("garbage", repaired_ids)

        # eviction removed the now-empty garbage row
        self.assertIn("garbage", {pid for pid, _ in report.evicted})
        self.assertNotIn("garbage", store.patterns)

        # dedup merged the duplicates: dup_b folded into dup_a
        self.assertEqual(len(report.dedup.merges_applied), 1)
        self.assertNotIn("dup_b", store.patterns)
        self.assertEqual(store.patterns["dup_a"]["support_count"], 3)  # E1,E2,E3

        # watermark advanced
        self.assertEqual(report.new_watermark, NOW)

    def test_dirty_and_duplicate_row_still_merges(self):
        # Regression: the survivor row is BOTH dirty (needs hygiene) AND a merge
        # target. Hygiene writes it (bumping its version); the later dedup merge
        # must use the refreshed token, not the stale pre-write one, or it would
        # 409 against our own hygiene write and the dirtiest rows would never
        # merge. dup_a has a bad support_count -> hygiene rewrites it; then dup_b
        # merges into it.
        dup_a = _pat("dup_a", 99, [E1, E2])         # support_count wrong -> hygiene fixes
        dup_b = _pat("dup_b", 1, [E3], domain_tags=["covid-19", "rct"])
        store = FakeStore([dup_a, dup_b], existing_event_ids={E1, E2, E3},
                          recall_map={"dup_a": ["dup_b"]})
        report = run_curator(store, apply=True, since=None, evict_min_support=1,
                             raw_judge=merge_yes("dup_a"), now_iso=NOW)
        # hygiene fixed dup_a's count; dedup still merged dup_b into it
        self.assertTrue(any(h["pattern_id"] == "dup_a" for h in report.hygiene_repaired))
        self.assertEqual(len(report.dedup.merges_applied), 1)
        self.assertEqual(report.dedup.conflicts, [])
        self.assertNotIn("dup_b", store.patterns)
        self.assertEqual(store.patterns["dup_a"]["support_count"], 3)

    def test_dry_run_writes_nothing(self):
        garbage = _pat("garbage", 1, ["drift_event_id_not_found_in_input"])
        store = FakeStore([garbage], existing_event_ids=set())
        before = dict(store.patterns["garbage"])
        report = run_curator(store, apply=False, since=None, now_iso=NOW)
        # reported as repairable + evictable, but nothing written/deleted
        self.assertTrue(report.hygiene_repaired)
        self.assertEqual(store.patterns["garbage"], before)
        self.assertEqual(store.deleted, [])

    def test_empty_scan_is_noop(self):
        store = FakeStore([], existing_event_ids=set())
        report = run_curator(store, apply=True, since="2099-01-01T00:00:00Z", now_iso=NOW)
        self.assertEqual(report.scanned, 0)
        self.assertIsNone(report.dedup)

    def test_referential_check_can_be_disabled(self):
        # well-formed but nonexistent id; with referential check OFF it survives
        p = _pat("p", 1, [E1])
        store = FakeStore([p], existing_event_ids=set())  # E1 NOT registered
        report = run_curator(store, apply=True, since=None, now_iso=NOW,
                             referential_check=False)
        self.assertEqual(report.hygiene_repaired, [])  # nothing stripped
        self.assertIn("p", store.patterns)

    def test_capped_dedup_holds_watermark(self):
        # When dedup caps, the watermark must NOT advance past the un-judged
        # window, or the leftover pairs are stranded forever. Use a `since` and
        # assert new_watermark stays at `since` when capped.
        SINCE = "2026-05-30T00:00:00Z"
        a = _pat("a", 3, [E1], last_updated_at=NOW)
        b = _pat("b", 2, [E2], last_updated_at=NOW)
        c = _pat("c", 1, [E3], last_updated_at=NOW)
        store = FakeStore([a, b, c], existing_event_ids={E1, E2, E3},
                          recall_map={"a": ["b", "c"], "b": ["a", "c"], "c": ["a", "b"]})

        def decline(payload):
            return {"same_phenomenon": False, "confidence": "low",
                    "merge_into_pattern_id": None, "merged_description": None,
                    "rationale": "no"}

        report = run_curator(store, apply=True, since=SINCE, evict_min_support=1,
                             raw_judge=decline, now_iso=NOW, max_judgments=2)
        self.assertTrue(report.dedup.judgments_capped)
        self.assertEqual(report.new_watermark, SINCE)  # held, not advanced to NOW

    def test_uncapped_dedup_advances_watermark(self):
        SINCE = "2026-05-30T00:00:00Z"
        a = _pat("a", 1, [E1], last_updated_at=NOW)
        store = FakeStore([a], existing_event_ids={E1})
        report = run_curator(store, apply=True, since=SINCE, evict_min_support=1,
                             now_iso=NOW, max_judgments=50)
        self.assertFalse(report.dedup.judgments_capped)
        self.assertEqual(report.new_watermark, NOW)  # advanced


if __name__ == "__main__":
    unittest.main()
