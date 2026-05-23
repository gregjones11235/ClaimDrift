"""
Elastic write helpers for the memory loop.

Memory Synthesizer (contracts.md §3.5) needs to persist a drift pattern after
each drift_event. There are two distinct business actions — `create` a new
pattern when none of the retrieved patterns is similar enough, and `update`
an existing pattern when one is. We expose them as two separate function
tools (`create_drift_pattern`, `update_drift_pattern`) rather than a single
upsert with a nullable id, so the create-vs-update decision is visible in
the tool name the LLM picks. See conversation log 2026-05-22.

Transport: reuses `ingestion.common.elastic.ElasticsearchHttpClient`. No
extra deps.

`record_source` policy: this module NEVER writes `record_source`. Per
contracts.md §2.3, real puller-ingested / agent-written docs leave the
field unset; the `"demo_seed"` tag is injected only by
`elastic/scripts/seed_demo_to_es.py` at write time.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# agents/_shared/ -> agents/ -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

load_dotenv(_REPO_ROOT / "agents" / ".env")

from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

DRIFT_PATTERNS_INDEX = "drift_patterns"

_VALID_PATTERN_TYPES = {
    "numerical_softening",
    "hedging_addition",
    "claim_disappearance",
    "effect_size_reduction",
    "other",
}


def _now_iso() -> str:
    """ISO 8601 UTC with Z suffix, per contracts.md §7.2."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_pattern_type(pattern_type: str) -> None:
    if pattern_type not in _VALID_PATTERN_TYPES:
        raise ValueError(
            f"pattern_type must be one of {sorted(_VALID_PATTERN_TYPES)}, "
            f"got {pattern_type!r}. See contracts.md §2.2.5."
        )


def create_drift_pattern(
    pattern_description: str,
    pattern_type: str,
    domain_tags: list[str],
    source_event_id: str,
) -> dict[str, Any]:
    """Create a new drift pattern in the `drift_patterns` index.

    Call this when retrieval surfaced no sufficiently-similar existing
    pattern — i.e. the new drift_event represents a previously-unseen
    kind of drift. If a similar pattern DOES exist, call
    `update_drift_pattern` instead so the existing pattern gains
    evidence rather than being shadowed by a near-duplicate.

    Args:
      pattern_description: Reusable, abstract summary of the drift
        pattern. This is the field future Drift Analyzer retrievals
        will ELSER-search against, so write it for retrieval, not for
        humans: include domain + drift type + rough magnitude; do NOT
        include specific DOIs, author names, or one-off details. 30-80
        words. Example: "COVID-related clinical preprints may show
        large effect-size reductions at publication with added
        hedging." See contracts.md §3.5.2.
      pattern_type: One of `numerical_softening` | `hedging_addition`
        | `claim_disappearance` | `effect_size_reduction` | `other`.
        See contracts.md §2.2.5.
      domain_tags: Keyword tags for filterable retrieval (e.g.
        `["covid-19", "clinical-trial", "rct"]`). Use lowercase with
        hyphens.
      source_event_id: The drift_event.event_id that triggered this
        pattern. Will be stored as a single-element list in
        `source_event_ids` (subsequent matching events grow this list
        via `update_drift_pattern`).

    Returns:
      The created pattern dict, including the freshly-minted
      `pattern_id` (UUID v4 per §7.2), `support_count=1`,
      `created_at == last_updated_at == now()`. Memory Synthesizer
      should pass this back in its §3.5.2 output as `pattern`.

    Raises:
      ValueError: invalid pattern_type or empty required fields.
      RuntimeError: Elasticsearch write failed.
    """
    if not pattern_description or not pattern_description.strip():
        raise ValueError("create_drift_pattern: pattern_description must be non-empty.")
    if not source_event_id or not source_event_id.strip():
        raise ValueError("create_drift_pattern: source_event_id must be non-empty.")
    _validate_pattern_type(pattern_type)

    pattern_id = str(uuid.uuid4())
    now = _now_iso()
    doc = {
        "pattern_id": pattern_id,
        "pattern_description": pattern_description.strip(),
        "pattern_type": pattern_type,
        "domain_tags": list(domain_tags),
        "source_event_ids": [source_event_id],
        "support_count": 1,
        "created_at": now,
        "last_updated_at": now,
    }

    client = ElasticsearchHttpClient()
    # Use op_type=create to detect the (vanishingly rare) UUID collision —
    # surfacing it as an error is better than silently overwriting an
    # unrelated pattern.
    client.request(
        "PUT",
        f"/{DRIFT_PATTERNS_INDEX}/_doc/{pattern_id}?op_type=create&refresh=wait_for",
        doc,
    )
    return doc


def update_drift_pattern(
    pattern_id: str,
    source_event_id: str,
) -> dict[str, Any]:
    """Append a new supporting drift_event to an existing pattern.

    Call this when retrieval surfaced an existing pattern that is
    similar enough to the new drift_event that they describe the same
    underlying phenomenon. Updates: appends `source_event_id` to
    `source_event_ids` (de-duped), recomputes `support_count =
    len(source_event_ids)`, and refreshes `last_updated_at`.

    v0 scope: this is the **minimum** update — appending evidence
    only. Refining `pattern_description` or extending `domain_tags`
    is intentionally deferred (see contracts.md §3.5.1 TODO). If you
    feel the existing description is too narrow, prefer
    `create_drift_pattern` for a sibling pattern; do not silently
    overwrite the existing one.

    Args:
      pattern_id: The `pattern_id` of an existing pattern, taken
        verbatim from a retrieved_patterns entry. Must exist —
        unknown ids raise rather than fall back to create, to keep
        the create-vs-update decision honest.
      source_event_id: The drift_event.event_id being merged in. If
        it's already in `source_event_ids` (a re-run on the same
        event), the document is left structurally unchanged but
        `last_updated_at` is still refreshed.

    Returns:
      The updated pattern dict (post-update state), suitable for
      Memory Synthesizer's §3.5.2 `pattern` output field.

    Raises:
      ValueError: missing required fields.
      RuntimeError: pattern_id not found, or Elasticsearch write
        failed.
    """
    if not pattern_id or not pattern_id.strip():
        raise ValueError("update_drift_pattern: pattern_id must be non-empty.")
    if not source_event_id or not source_event_id.strip():
        raise ValueError("update_drift_pattern: source_event_id must be non-empty.")

    client = ElasticsearchHttpClient()

    # GET-then-PUT keeps the merge logic in Python (de-dup, count
    # recompute) — simpler than an Elasticsearch painless script and
    # the index is tiny.
    try:
        existing = client.request("GET", f"/{DRIFT_PATTERNS_INDEX}/_doc/{pattern_id}")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            raise RuntimeError(
                f"update_drift_pattern: pattern_id {pattern_id!r} not found. "
                f"If this is a new pattern, call create_drift_pattern instead."
            ) from exc
        raise

    source = existing.get("_source") or {}
    source_event_ids = list(source.get("source_event_ids") or [])
    if source_event_id not in source_event_ids:
        source_event_ids.append(source_event_id)

    description = source.get("pattern_description")
    # semantic_text fields read back as objects on serverless; collapse to text
    # so the round-trip PUT body is well-formed.
    if isinstance(description, dict):
        description = description.get("text") or description.get("value") or ""

    updated = {
        "pattern_id": source.get("pattern_id", pattern_id),
        "pattern_description": description,
        "pattern_type": source.get("pattern_type"),
        "domain_tags": list(source.get("domain_tags") or []),
        "source_event_ids": source_event_ids,
        "support_count": len(source_event_ids),
        "created_at": source.get("created_at"),
        "last_updated_at": _now_iso(),
    }

    client.request(
        "PUT",
        f"/{DRIFT_PATTERNS_INDEX}/_doc/{pattern_id}?refresh=wait_for",
        updated,
    )
    return updated
