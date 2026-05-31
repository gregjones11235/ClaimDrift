"""JSON-Schema for the curator's one LLM call (contracts.md §3.6.3).

The LLM judges "are candidate_a and candidate_b the same underlying drift
phenomenon?" and, if so, proposes a single merged description. Its output is
schema-gated TWICE before any write: once by Gemini's own structured-output
(response_schema) and again here by jsonschema (the deterministic guardrail
that wraps the LLM — design B.1). Code, never the LLM, executes the merge.

Only the OUTPUT is validated as a guardrail. The INPUT schema is kept for
documentation / test fixtures; we build the input ourselves so it is trusted.
"""
from __future__ import annotations

from typing import Any

# §2.2.5 pattern_type enum (v2: includes outcome_switch).
PATTERN_TYPES = [
    "numerical_softening",
    "hedging_addition",
    "claim_disappearance",
    "effect_size_reduction",
    "outcome_switch",
    "other",
]

# §3.6.3 INPUT (one candidate pair). Documentation / fixture shape.
LLM_JUDGE_INPUT: dict[str, Any] = {
    "type": "object",
    "required": ["candidate_a", "candidate_b"],
    "properties": {
        "candidate_a": {"$ref": "#/$defs/candidate"},
        "candidate_b": {"$ref": "#/$defs/candidate"},
    },
    "$defs": {
        "candidate": {
            "type": "object",
            "required": ["pattern_id", "pattern_description", "pattern_type",
                         "domain_tags", "support_count"],
            "properties": {
                "pattern_id": {"type": "string"},
                "pattern_description": {"type": "string"},
                "pattern_type": {"type": "string"},
                "domain_tags": {"type": "array", "items": {"type": "string"}},
                "support_count": {"type": "integer"},
            },
        },
    },
}

# §3.6.3 OUTPUT — the guardrail schema. Deliberately strict: this gates a
# WRITE to the base-rate store, so a malformed proposal must be rejected (and
# the conservative default — do not merge — kicks in).
LLM_JUDGE_OUTPUT: dict[str, Any] = {
    "type": "object",
    "required": ["same_phenomenon", "confidence", "merge_into_pattern_id",
                 "merged_description", "rationale"],
    "additionalProperties": False,
    "properties": {
        "same_phenomenon": {"type": "boolean"},
        "confidence": {"enum": ["high", "medium", "low"]},
        # null when same_phenomenon is false (code keeps both rows).
        "merge_into_pattern_id": {"type": ["string", "null"]},
        "merged_description": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
    },
}
