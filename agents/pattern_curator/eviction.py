"""Deterministic low-quality / orphan pattern eviction (C3 / D3, §3.6.2).

Pure code, no LLM. A filtered selection — never a full scan — returns a small
set of patterns to evict, mirroring the targeted-delete discipline of
cleanup_probe_patterns.py (delete only an explicitly-selected id set, so it can
never over-delete).

Eviction signals (all deterministic, available on every row):
  - low support: support_count < min_support after hygiene recomputed it. A
    pattern backed by too few real events is not a trustworthy base rate.
  - stale orphan: support_count is at the floor (1) AND last_updated_at is
    older than max_age. "Never retrieved" is not directly tracked per-row, so
    we proxy it as "minimal evidence that also stopped accumulating" — a row
    that gained no new events for a long time and never grew past one.

A demo_seed row is NEVER evicted (it is hand-curated fixture data, §2.3).

The function only SELECTS; the curator performs the targeted delete via
es_ops.delete_by_ids. We return the ids + a per-id reason for the report.
"""
from __future__ import annotations

from typing import Any


def select_evictions(
    patterns: list[dict[str, Any]],
    *,
    min_support: int = 1,
    stale_orphan_age_iso: str | None = None,
) -> list[tuple[str, str]]:
    """Return [(pattern_id, reason), ...] for patterns that should be evicted.

    Args:
      patterns: the scanned (ideally post-hygiene) patterns, each a dict with
        pattern_id / support_count / last_updated_at / record_source.
      min_support: evict if support_count < this. Default 1 means "evict only
        rows with 0 support" (an empty pattern — all its source_event_ids were
        hygiene-stripped as hallucinations). Raise to be more aggressive.
      stale_orphan_age_iso: if set, a support_count==1 row whose
        last_updated_at is strictly older than this ISO cutoff is evicted as a
        stale orphan. None disables the stale-orphan rule (only the empty-row
        rule applies).

    Selection is conservative by construction: the defaults evict only patterns
    that hygiene reduced to zero real evidence. Everything else requires an
    explicit, caller-supplied age cutoff.
    """
    evictions: list[tuple[str, str]] = []
    for p in patterns:
        if p.get("record_source") == "demo_seed":
            continue  # never evict hand-curated fixtures
        pattern_id = p.get("pattern_id")
        if not pattern_id:
            continue
        support = p.get("support_count")
        support = support if isinstance(support, int) else 0

        if support < min_support:
            evictions.append(
                (pattern_id, f"support_count {support} < min_support {min_support} "
                             f"(no trustworthy base rate)")
            )
            continue

        if (
            stale_orphan_age_iso is not None
            and support <= 1
            and isinstance(p.get("last_updated_at"), str)
            and p["last_updated_at"] < stale_orphan_age_iso
        ):
            evictions.append(
                (pattern_id, f"stale orphan: support_count {support}, "
                             f"last_updated_at {p['last_updated_at']} < {stale_orphan_age_iso}")
            )

    return evictions
