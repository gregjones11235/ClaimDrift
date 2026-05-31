"""Deterministic data hygiene for drift_patterns (C3 / D3, §3.6.2).

Pure code, no LLM. Repairs the exact garbage the captured memory.json showed
(memory_loop_v2_design.md §0.3): a hallucinated source_event_id
(`"drift_event_id_not_found_in_input"`), a fabricated timestamp, and a
support_count that does not match the real event count. The §3.5.1 invariant is
`support_count == len(source_event_ids)`; we enforce it deterministically here.

A source_event_id is validated in TWO deterministic layers — both pure code,
neither an LLM:

  1. STRUCTURAL — is it a real UUID v4 and not a known sentinel? (regex)
     Catches `"drift_event_id_not_found_in_input"` and friends.
  2. REFERENTIAL — does a drift_event with that id actually exist? (an ES
     `terms` lookup, injected as `event_id_exists` so this stays pure/testable)
     Catches a well-formed-but-fabricated UUID the regex alone would pass.

The "is this the same phenomenon / is the description stale" judgments are the
LLM's job and live in dedup.py — NOT here. Hygiene never reasons about meaning.

Each function returns a (possibly) repaired copy + the fixes applied; nothing
writes to ES — the curator persists via es_ops with optimistic concurrency.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

# A real drift_event_id is a UUID v4 (contracts.md §7.2). Anything that is not a
# UUID is a hallucination/sentinel (e.g. "drift_event_id_not_found_in_input").
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
# Known sentinel strings observed in the wild; rejected regardless of shape.
_SENTINELS = {
    "drift_event_id_not_found_in_input",
    "unknown",
    "null",
    "none",
    "",
}

# ISO 8601 UTC with Z (contracts.md §7.2). Shape check only — we can't tell a
# fabricated-but-well-formed timestamp from its value, which is exactly why the
# real defenses are event_id validity + referential integrity + count recompute.
_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Injected referential check: given a set of structurally-valid ids, return the
# subset that actually exists in drift_events (es_ops.PatternStore.existing_event_ids).
EventIdExistsCheck = Callable[[list[str]], "set[str]"]


def is_structurally_valid_event_id(event_id: Any) -> bool:
    """Layer 1: True iff event_id is a real UUID and not a known sentinel."""
    if not isinstance(event_id, str):
        return False
    if event_id.strip().lower() in _SENTINELS:
        return False
    return bool(_UUID_RE.match(event_id.strip()))


def hygiene_pattern(
    pattern: dict[str, Any],
    now_iso: str,
    existing_ids: Iterable[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return (repaired_pattern, fixes_applied).

    Deterministic repairs, in order:
      1a. drop structurally-invalid source_event_ids (non-UUID / sentinel);
      1b. drop structurally-valid-but-nonexistent ids — the ids NOT present in
          `existing_ids` (the referential-integrity result). If `existing_ids`
          is None, the referential layer is skipped (structural-only mode,
          e.g. when drift_events is unavailable);
      2.  recompute support_count = len(surviving source_event_ids) (§3.5.1);
      3.  fill a malformed/missing created_at / last_updated_at with now_iso.
          We never invent a *plausible past* timestamp — that is the same
          fabrication we remove; we stamp the honest repair time.

    `now_iso` is injected (not read from a clock) so the function is pure and
    reproducible — same rationale as the orchestrator-fills-timestamp
    convention in contracts.md §3.2.2. `existing_ids` is the referential
    layer's output, also injected for testability.
    """
    fixes: list[str] = []
    repaired = dict(pattern)
    existing_set = set(existing_ids) if existing_ids is not None else None

    raw_ids = list(repaired.get("source_event_ids") or [])
    clean_ids: list[str] = []
    seen: set[str] = set()
    dropped_structural: list[str] = []
    dropped_referential: list[str] = []

    for eid in raw_ids:
        if not is_structurally_valid_event_id(eid):
            dropped_structural.append(str(eid))
            continue
        key = eid.strip()
        if existing_set is not None and key not in existing_set:
            dropped_referential.append(key)
            continue
        if key not in seen:
            seen.add(key)
            clean_ids.append(key)

    if dropped_structural:
        fixes.append(
            f"dropped {len(dropped_structural)} structurally-invalid "
            f"source_event_id(s): {dropped_structural}"
        )
    if dropped_referential:
        fixes.append(
            f"dropped {len(dropped_referential)} source_event_id(s) with no "
            f"backing drift_event: {dropped_referential}"
        )
    if len(clean_ids) != len(raw_ids):
        repaired["source_event_ids"] = clean_ids

    # 2. recompute support_count (§3.5.1 invariant)
    expected = len(clean_ids)
    if repaired.get("support_count") != expected:
        fixes.append(
            f"recomputed support_count {repaired.get('support_count')} -> {expected} "
            f"(= len(source_event_ids))"
        )
        repaired["support_count"] = expected

    # 3. fill malformed/missing timestamps with the honest repair time
    for field in ("created_at", "last_updated_at"):
        val = repaired.get(field)
        if not (isinstance(val, str) and _ISO_Z_RE.match(val)):
            fixes.append(f"filled malformed/missing {field} {val!r} -> {now_iso}")
            repaired[field] = now_iso

    return repaired, fixes


def collect_structural_event_ids(patterns: list[dict[str, Any]]) -> list[str]:
    """All structurally-valid source_event_ids across patterns, de-duped.

    The curator passes these to the referential check in ONE batched ES query
    (es_ops.existing_event_ids) rather than querying per pattern."""
    out: set[str] = set()
    for p in patterns:
        for eid in (p.get("source_event_ids") or []):
            if is_structurally_valid_event_id(eid):
                out.add(eid.strip())
    return sorted(out)


def needs_persist(fixes: list[str]) -> bool:
    """A pattern needs a write back only if hygiene actually changed something."""
    return bool(fixes)
