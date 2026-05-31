"""The curator's ONE LLM call: "are these two patterns the same phenomenon?"
(C3 / D3, contracts.md §3.6.3 / §3.6.4).

This is the only AI touchpoint in the whole curator. Everything around it is
deterministic code (recall, hygiene, eviction, the actual ES writes). The call
is wrapped by guardrails so a bad LLM output can never corrupt the base-rate
store:

  - Gemini is asked for STRUCTURED output (response_schema = §3.6.3 output), so
    the model is constrained at generation time.
  - The returned JSON is then re-validated by jsonschema (the deterministic
    gate) — belt and suspenders; a structured-output miss still gets caught.
  - CONSERVATIVE DEFAULT: any failure (API error, unparseable output, schema
    miss, confidence below "high") collapses to do_not_merge. Wrongly merging
    two distinct phenomena corrupts the base rate, which is worse than leaving
    a duplicate (§3.6.2). So errors bias toward keeping both rows, never toward
    a wrong merge.

The Gemini client is INJECTED (defaults to a lazily-built google.genai client
on the Vertex backend) so unit tests pass a fake judge and run with zero tokens.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .prompts import DEDUP_JUDGE_PROMPT
from .schemas import LLM_JUDGE_OUTPUT

# Gemini model for the judgment. Pro is justified: this is the one semantic call
# and a wrong merge is expensive. Overridable via env to match the rest of the
# fleet's config convention (drift_analyzer/agent.py MODEL_PRO).
MODEL = os.getenv("CLAIMDRIFT_CURATOR_MODEL", os.getenv("CLAIMDRIFT_MODEL_PRO", "gemini-2.5-pro"))

_VALIDATOR = Draft202012Validator(LLM_JUDGE_OUTPUT)


@dataclass(frozen=True)
class MergeDecision:
    """The validated, guardrail-applied decision the curator code acts on."""

    same_phenomenon: bool
    confidence: str           # high | medium | low
    merge_into_pattern_id: str | None
    merged_description: str | None
    rationale: str
    # True only when ALL of: same_phenomenon, confidence == "high", a valid
    # surviving id, and a non-empty merged_description. The curator merges iff
    # this is True — the single source of truth for "should code merge?".
    should_merge: bool


# A RawJudge takes the §3.6.3 input dict and returns the model's raw JSON text
# (or a dict). Injected so tests don't call Vertex. Production default below.
RawJudge = Callable[[dict[str, Any]], "str | dict | None"]


def _conservative(rationale: str) -> MergeDecision:
    """The do-not-merge default used for every failure mode."""
    return MergeDecision(
        same_phenomenon=False,
        confidence="low",
        merge_into_pattern_id=None,
        merged_description=None,
        rationale=rationale,
        should_merge=False,
    )


def decide_merge(
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
    raw_judge: RawJudge | None = None,
) -> MergeDecision:
    """Run the one LLM judgment for a candidate pair and apply all guardrails.

    Returns a MergeDecision. NEVER raises for an LLM/parse/schema problem — all
    such failures resolve to the conservative do-not-merge default with a
    rationale that says why, so the curator can log it. (Programmer errors like
    a missing pattern_id in the input still raise via KeyError, surfacing a
    real bug rather than silently mis-merging.)
    """
    judge = raw_judge or _default_vertex_judge
    payload = {
        "candidate_a": _project_candidate(candidate_a),
        "candidate_b": _project_candidate(candidate_b),
    }

    try:
        raw = judge(payload)
    except Exception as exc:  # API/transport error -> conservative
        return _conservative(f"LLM call failed; not merging ({type(exc).__name__}: {exc})")

    if raw is None:
        return _conservative("LLM returned nothing; not merging")

    if isinstance(raw, str):
        try:
            obj = json.loads(_strip_fence(raw))
        except (json.JSONDecodeError, TypeError):
            return _conservative("LLM output was not valid JSON; not merging")
    elif isinstance(raw, dict):
        obj = raw
    else:
        return _conservative(f"LLM output was unexpected type {type(raw).__name__}; not merging")

    # deterministic schema gate (§3.6.3 output)
    errors = sorted(_VALIDATOR.iter_errors(obj), key=lambda e: list(e.path))
    if errors:
        return _conservative(f"LLM output failed §3.6.3 schema gate: {errors[0].message}")

    return _apply_policy(obj, candidate_a, candidate_b)


def _apply_policy(
    obj: dict[str, Any],
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
) -> MergeDecision:
    """Turn a schema-valid LLM proposal into a guarded MergeDecision.

    should_merge requires ALL of (conservative default — §3.6.4):
      - same_phenomenon is true,
      - confidence == "high" (anything below high is treated as do-not-merge),
      - merge_into_pattern_id is one of the two candidate ids (the surviving
        row must actually be one of the pair — the LLM cannot invent an id),
      - merged_description is a non-empty string.
    Any miss => keep both rows; we still surface the LLM's stated reasoning.
    """
    same = bool(obj.get("same_phenomenon"))
    confidence = obj.get("confidence", "low")
    survivor = obj.get("merge_into_pattern_id")
    merged_desc = obj.get("merged_description")
    rationale = obj.get("rationale", "")

    valid_ids = {candidate_a["pattern_id"], candidate_b["pattern_id"]}
    should_merge = (
        same
        and confidence == "high"
        and isinstance(survivor, str)
        and survivor in valid_ids
        and isinstance(merged_desc, str)
        and bool(merged_desc.strip())
    )

    return MergeDecision(
        same_phenomenon=same,
        confidence=confidence,
        merge_into_pattern_id=survivor if should_merge else None,
        merged_description=merged_desc.strip() if should_merge else None,
        rationale=rationale,
        should_merge=should_merge,
    )


def _project_candidate(c: dict[str, Any]) -> dict[str, Any]:
    """The §3.6.3 input candidate shape (only the fields the prompt needs)."""
    return {
        "pattern_id": c["pattern_id"],
        "pattern_description": c.get("pattern_description", ""),
        "pattern_type": c.get("pattern_type"),
        "domain_tags": list(c.get("domain_tags") or []),
        "support_count": c.get("support_count", 0),
    }


def _strip_fence(text: str) -> str:
    """Drop a leading ```json / ``` fence and trailing ``` if present (Gemini
    wraps JSON in fences regardless of instructions — same lesson as
    supervisor_agent/agent.py:_strip_markdown_fence)."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _vertex_response_schema():
    """Build the §3.6.3 output schema as a google.genai types.Schema.

    Vertex's response_schema does NOT accept JSON-Schema's array-of-types
    nullable form (`"type": ["string", "null"]`) — it wants a single type plus
    `nullable=True` (verified against the live cluster: a type-array raised a
    pydantic enum error). So the structured-output constraint is expressed in
    Vertex's dialect here, while the deterministic gate (LLM_JUDGE_OUTPUT, plain
    jsonschema) still validates the returned JSON. Two dialects, one contract.
    """
    from google.genai import types

    T = types.Type
    return types.Schema(
        type=T.OBJECT,
        required=["same_phenomenon", "confidence", "merge_into_pattern_id",
                  "merged_description", "rationale"],
        properties={
            "same_phenomenon": types.Schema(type=T.BOOLEAN),
            "confidence": types.Schema(type=T.STRING, enum=["high", "medium", "low"]),
            "merge_into_pattern_id": types.Schema(type=T.STRING, nullable=True),
            "merged_description": types.Schema(type=T.STRING, nullable=True),
            "rationale": types.Schema(type=T.STRING),
        },
    )


def _default_vertex_judge(payload: dict[str, Any]) -> str:
    """Production judge: one google.genai call on the Vertex backend, asking
    for structured output constrained to the §3.6.3 output schema. Built lazily
    so importing this module needs no credentials (tests inject a fake)."""
    from google import genai
    from google.genai import types

    client = genai.Client()  # reads GOOGLE_CLOUD_PROJECT/LOCATION/USE_VERTEXAI
    prompt = DEDUP_JUDGE_PROMPT.format(input_json=json.dumps(payload, ensure_ascii=False, indent=2))
    resp = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_vertex_response_schema(),
            temperature=0.0,  # deterministic-leaning; this is a judgment, not generation
        ),
    )
    return resp.text
