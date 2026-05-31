"""LLM-judge guardrail tests (C3 / D3). Fake judge -> zero tokens.

The point of these tests is the GUARDRAILS, not the model: every failure mode
must collapse to the conservative do-not-merge default, and only an
unambiguous high-confidence valid proposal yields should_merge=True.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
if str(_AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTS_ROOT))

from pattern_curator.llm_judge import decide_merge  # noqa: E402

A = {"pattern_id": "uuid-a", "pattern_description": "COVID RCT effect-size reduction",
     "pattern_type": "effect_size_reduction", "domain_tags": ["covid-19", "clinical-trial"],
     "support_count": 12}
B = {"pattern_id": "uuid-b", "pattern_description": "COVID RCT effect-size reduction",
     "pattern_type": "effect_size_reduction", "domain_tags": ["covid-19", "rct"],
     "support_count": 8}


def judge_returning(obj):
    return lambda payload: obj


class LlmJudgeTest(unittest.TestCase):
    def test_valid_high_confidence_merge(self):
        d = decide_merge(A, B, raw_judge=judge_returning({
            "same_phenomenon": True, "confidence": "high",
            "merge_into_pattern_id": "uuid-a",
            "merged_description": "COVID clinical RCT preprints often show large "
                                  "effect-size reductions at publication.",
            "rationale": "both large COVID RCT reductions",
        }))
        self.assertTrue(d.should_merge)
        self.assertEqual(d.merge_into_pattern_id, "uuid-a")

    def test_medium_confidence_does_not_merge(self):
        d = decide_merge(A, B, raw_judge=judge_returning({
            "same_phenomenon": True, "confidence": "medium",
            "merge_into_pattern_id": "uuid-a",
            "merged_description": "x" * 40, "rationale": "unsure",
        }))
        self.assertFalse(d.should_merge)

    def test_same_phenomenon_false_does_not_merge(self):
        d = decide_merge(A, B, raw_judge=judge_returning({
            "same_phenomenon": False, "confidence": "high",
            "merge_into_pattern_id": None, "merged_description": None,
            "rationale": "different domains",
        }))
        self.assertFalse(d.should_merge)

    def test_survivor_id_must_be_a_candidate(self):
        # LLM proposes an id that is neither candidate -> reject (can't invent ids)
        d = decide_merge(A, B, raw_judge=judge_returning({
            "same_phenomenon": True, "confidence": "high",
            "merge_into_pattern_id": "uuid-ZZZ",
            "merged_description": "x" * 40, "rationale": "hallucinated id",
        }))
        self.assertFalse(d.should_merge)

    def test_schema_violation_is_conservative(self):
        # missing required keys -> schema gate fails -> do not merge
        d = decide_merge(A, B, raw_judge=judge_returning({"same_phenomenon": True}))
        self.assertFalse(d.should_merge)
        self.assertIn("schema gate", d.rationale)

    def test_unparseable_string_is_conservative(self):
        d = decide_merge(A, B, raw_judge=lambda p: "not json at all")
        self.assertFalse(d.should_merge)
        self.assertIn("not valid JSON", d.rationale)

    def test_fenced_json_is_parsed(self):
        d = decide_merge(A, B, raw_judge=lambda p: (
            '```json\n{"same_phenomenon": false, "confidence": "low", '
            '"merge_into_pattern_id": null, "merged_description": null, '
            '"rationale": "fenced"}\n```'
        ))
        self.assertFalse(d.should_merge)
        self.assertEqual(d.rationale, "fenced")

    def test_llm_exception_is_conservative(self):
        def boom(payload):
            raise RuntimeError("vertex 500")
        d = decide_merge(A, B, raw_judge=boom)
        self.assertFalse(d.should_merge)
        self.assertIn("LLM call failed", d.rationale)

    def test_empty_merged_description_does_not_merge(self):
        d = decide_merge(A, B, raw_judge=judge_returning({
            "same_phenomenon": True, "confidence": "high",
            "merge_into_pattern_id": "uuid-a", "merged_description": "   ",
            "rationale": "blank desc",
        }))
        self.assertFalse(d.should_merge)


if __name__ == "__main__":
    unittest.main()
