"""Eviction unit tests (C3 / D3). Pure, deterministic."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from pattern_curator import eviction  # noqa: E402


class EvictionTest(unittest.TestCase):
    def test_evicts_empty_row(self):
        pats = [{"pattern_id": "empty", "support_count": 0, "last_updated_at": "2026-01-01T00:00:00Z"}]
        ev = eviction.select_evictions(pats, min_support=1)
        self.assertEqual([pid for pid, _ in ev], ["empty"])

    def test_keeps_supported_row(self):
        pats = [{"pattern_id": "ok", "support_count": 5, "last_updated_at": "2026-01-01T00:00:00Z"}]
        self.assertEqual(eviction.select_evictions(pats, min_support=1), [])

    def test_min_support_threshold(self):
        pats = [{"pattern_id": "two", "support_count": 2, "last_updated_at": "2026-01-01T00:00:00Z"}]
        self.assertEqual(eviction.select_evictions(pats, min_support=2), [])
        ev = eviction.select_evictions(pats, min_support=3)
        self.assertEqual([pid for pid, _ in ev], ["two"])

    def test_stale_orphan_rule(self):
        pats = [
            {"pattern_id": "old1", "support_count": 1, "last_updated_at": "2025-01-01T00:00:00Z"},
            {"pattern_id": "new1", "support_count": 1, "last_updated_at": "2026-05-01T00:00:00Z"},
        ]
        ev = eviction.select_evictions(pats, min_support=1, stale_orphan_age_iso="2026-01-01T00:00:00Z")
        ids = [pid for pid, _ in ev]
        self.assertIn("old1", ids)        # old + support 1 -> stale orphan
        self.assertNotIn("new1", ids)     # recent -> kept

    def test_never_evicts_demo_seed(self):
        pats = [{"pattern_id": "seed", "support_count": 0, "record_source": "demo_seed",
                 "last_updated_at": "2020-01-01T00:00:00Z"}]
        ev = eviction.select_evictions(pats, min_support=1,
                                       stale_orphan_age_iso="2026-01-01T00:00:00Z")
        self.assertEqual(ev, [])

    def test_stale_orphan_disabled_when_no_cutoff(self):
        pats = [{"pattern_id": "old1", "support_count": 1, "last_updated_at": "2020-01-01T00:00:00Z"}]
        # min_support=1 keeps support==1; no stale cutoff -> not evicted
        self.assertEqual(eviction.select_evictions(pats, min_support=1), [])


if __name__ == "__main__":
    unittest.main()
