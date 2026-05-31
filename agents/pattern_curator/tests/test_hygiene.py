"""Hygiene unit tests (C3 / D3). Pure, deterministic, no ES/LLM."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from pattern_curator import hygiene  # noqa: E402

NOW = "2026-05-31T00:00:00Z"
UUID_A = "a1b2c3d4-e5f6-4789-abcd-1234567890ab"
UUID_B = "b2c3d4e5-f6a7-4890-bcde-2345678901bc"


class HygieneTest(unittest.TestCase):
    def test_drops_sentinel_event_id(self):
        p = {"pattern_id": "p1", "source_event_ids": ["drift_event_id_not_found_in_input"],
             "support_count": 1, "created_at": NOW, "last_updated_at": NOW}
        repaired, fixes = hygiene.hygiene_pattern(p, NOW, existing_ids={UUID_A})
        self.assertEqual(repaired["source_event_ids"], [])
        self.assertEqual(repaired["support_count"], 0)
        self.assertTrue(any("structurally-invalid" in f for f in fixes))

    def test_drops_wellformed_but_nonexistent_uuid(self):
        # UUID_B is structurally valid but NOT in existing_ids -> referential drop
        p = {"pattern_id": "p1", "source_event_ids": [UUID_A, UUID_B],
             "support_count": 2, "created_at": NOW, "last_updated_at": NOW}
        repaired, fixes = hygiene.hygiene_pattern(p, NOW, existing_ids={UUID_A})
        self.assertEqual(repaired["source_event_ids"], [UUID_A])
        self.assertEqual(repaired["support_count"], 1)
        self.assertTrue(any("backing drift_event" in f for f in fixes))

    def test_recomputes_support_count(self):
        p = {"pattern_id": "p1", "source_event_ids": [UUID_A, UUID_B],
             "support_count": 99, "created_at": NOW, "last_updated_at": NOW}
        repaired, fixes = hygiene.hygiene_pattern(p, NOW, existing_ids={UUID_A, UUID_B})
        self.assertEqual(repaired["support_count"], 2)
        self.assertTrue(any("recomputed support_count" in f for f in fixes))

    def test_dedupes_event_ids(self):
        p = {"pattern_id": "p1", "source_event_ids": [UUID_A, UUID_A, UUID_B],
             "support_count": 3, "created_at": NOW, "last_updated_at": NOW}
        repaired, _ = hygiene.hygiene_pattern(p, NOW, existing_ids={UUID_A, UUID_B})
        self.assertEqual(repaired["source_event_ids"], [UUID_A, UUID_B])
        self.assertEqual(repaired["support_count"], 2)

    def test_fills_fabricated_timestamp_shape(self):
        # a malformed timestamp gets replaced with the honest repair time
        p = {"pattern_id": "p1", "source_event_ids": [UUID_A], "support_count": 1,
             "created_at": "2023-10-27", "last_updated_at": "not-a-date"}
        repaired, fixes = hygiene.hygiene_pattern(p, NOW, existing_ids={UUID_A})
        self.assertEqual(repaired["created_at"], NOW)
        self.assertEqual(repaired["last_updated_at"], NOW)
        self.assertEqual(sum("filled malformed/missing" in f for f in fixes), 2)

    def test_clean_pattern_yields_no_fixes(self):
        p = {"pattern_id": "p1", "source_event_ids": [UUID_A], "support_count": 1,
             "created_at": NOW, "last_updated_at": NOW}
        repaired, fixes = hygiene.hygiene_pattern(p, NOW, existing_ids={UUID_A})
        self.assertEqual(fixes, [])
        self.assertFalse(hygiene.needs_persist(fixes))

    def test_structural_only_mode_skips_referential(self):
        # existing_ids=None -> referential layer skipped; structurally-valid id kept
        p = {"pattern_id": "p1", "source_event_ids": [UUID_B], "support_count": 1,
             "created_at": NOW, "last_updated_at": NOW}
        repaired, fixes = hygiene.hygiene_pattern(p, NOW, existing_ids=None)
        self.assertEqual(repaired["source_event_ids"], [UUID_B])
        self.assertEqual(fixes, [])

    def test_collect_structural_event_ids(self):
        pats = [
            {"source_event_ids": [UUID_A, "bad"]},
            {"source_event_ids": [UUID_A, UUID_B]},
        ]
        ids = hygiene.collect_structural_event_ids(pats)
        self.assertEqual(ids, sorted({UUID_A, UUID_B}))


if __name__ == "__main__":
    unittest.main()
