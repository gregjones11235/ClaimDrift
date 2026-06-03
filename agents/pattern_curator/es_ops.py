"""Deterministic Elasticsearch primitives for the pattern_curator (C3 / D3).

Pure code, no LLM. Owns every read/write the curator does against the
`drift_patterns` index:

  - incremental scan by `last_updated_at` high-watermark (§3.6.1: routine cost
    is O(new-since-last-run), never O(index size));
  - deterministic duplicate RECALL (keyword pre-filter + ELSER neighbor
    search) — recall only, the same-phenomenon JUDGMENT is the one LLM call;
  - optimistic-concurrency writes (`if_seq_no` / `if_primary_term`) so a
    curator merge can never clobber an in-flight memory_synthesizer append
    (design B.1 guardrail);
  - targeted delete reusing the cleanup_probe_patterns.py filter pattern.

Curator writes go to the CONCRETE `drift_patterns` index in place (the decided
write target): routine governance touches few rows and uses optimistic
concurrency; only a rare human-triggered taxonomy backfill goes through the C1
shadow-index/alias rebuild (manage_pattern_alias.py), never this module.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_AGENTS_ROOT, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _shared.elastic_retrieval import DRIFT_PATTERNS_INDEX  # noqa: E402
from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

# drift_events index — hygiene's referential-integrity check verifies each
# source_event_id actually exists here (catches well-formed-but-fabricated
# UUIDs that the regex shape check alone would pass).
DRIFT_EVENTS_INDEX = "drift_events"

# Fields we round-trip; mirrors drift_patterns.json. pattern_description is a
# semantic_text field that reads back as {"text": ...} on serverless — callers
# collapse it via flatten_description().
_SOURCE_FIELDS = [
    "pattern_id",
    "pattern_description",
    "pattern_type",
    "domain_tags",
    "source_event_ids",
    "support_count",
    "created_at",
    "last_updated_at",
    "record_source",
]


def flatten_description(value: Any) -> str:
    """semantic_text reads back as an object on serverless; collapse to text."""
    if isinstance(value, dict):
        return value.get("text") or value.get("value") or ""
    return value or ""


def _hit_to_pattern(hit: dict[str, Any]) -> dict[str, Any]:
    """Project an ES hit into a plain pattern dict + concurrency tokens.

    The `_seq_no` / `_primary_term` are carried so a later write can assert
    optimistic concurrency against the exact version we read.
    """
    src = dict(hit.get("_source") or {})
    src["pattern_description"] = flatten_description(src.get("pattern_description"))
    src["_seq_no"] = hit.get("_seq_no")
    src["_primary_term"] = hit.get("_primary_term")
    return src


class PatternStore:
    """Thin wrapper over ElasticsearchHttpClient for curator reads/writes.

    Injectable client makes the whole curator unit-testable with a fake ES.
    """

    def __init__(self, client: ElasticsearchHttpClient | None = None, index: str | None = None):
        self.client = client or ElasticsearchHttpClient()
        self.index = index or DRIFT_PATTERNS_INDEX

    # --- reads --------------------------------------------------------------
    def scan_since(self, watermark_iso: str | None, size: int = 500) -> list[dict[str, Any]]:
        """Return patterns with `last_updated_at` >= watermark (all if None).

        Incremental hygiene/dedup scan. `seq_no_primary_term: true` so each
        returned pattern carries the tokens needed for an OCC write.
        """
        query: dict[str, Any]
        if watermark_iso:
            query = {"range": {"last_updated_at": {"gte": watermark_iso}}}
        else:
            query = {"match_all": {}}
        body = {
            "query": query,
            "size": size,
            "sort": [{"last_updated_at": "asc"}],
            "seq_no_primary_term": True,
            "_source": _SOURCE_FIELDS,
        }
        resp = self.client.request("POST", f"/{self.index}/_search", body)
        hits = (resp.get("hits") or {}).get("hits") or []
        return [_hit_to_pattern(h) for h in hits]

    def recall_duplicate_candidates(
        self,
        pattern: dict[str, Any],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Deterministic duplicate RECALL for one pattern (§3.6.1).

        Keyword pre-filter (same pattern_type AND domain_tags overlap) THEN
        ELSER semantic neighbor search on pattern_description. Recall only —
        the same-phenomenon decision is the LLM's. Excludes the pattern itself
        and demo_seed rows. Returns neighbors with concurrency tokens.
        """
        pattern_type = pattern.get("pattern_type")
        domain_tags = pattern.get("domain_tags") or []
        description = flatten_description(pattern.get("pattern_description"))
        self_id = pattern.get("pattern_id")
        if not pattern_type or not description.strip():
            return []

        filters: list[dict[str, Any]] = [{"term": {"pattern_type": pattern_type}}]
        if domain_tags:
            # domain_tags overlap: at least one shared tag.
            filters.append({"terms": {"domain_tags": list(domain_tags)}})

        must_not: list[dict[str, Any]] = [
            {"term": {"pattern_id": self_id}},
            {"term": {"record_source": "demo_seed"}},
        ]

        body = {
            "size": top_k,
            "seq_no_primary_term": True,
            "_source": _SOURCE_FIELDS,
            "query": {
                "bool": {
                    "filter": filters,
                    "must_not": must_not,
                    "must": [
                        {"semantic": {"field": "pattern_description", "query": description}}
                    ],
                }
            },
        }
        resp = self.client.request("POST", f"/{self.index}/_search", body)
        hits = (resp.get("hits") or {}).get("hits") or []
        return [_hit_to_pattern(h) for h in hits]

    def get_by_id(self, pattern_id: str) -> dict[str, Any] | None:
        try:
            resp = self.client.request("GET", f"/{self.index}/_doc/{pattern_id}")
        except RuntimeError as exc:
            if "404" in str(exc):
                return None
            raise
        if not resp.get("found", True):
            return None
        src = dict(resp.get("_source") or {})
        src["pattern_description"] = flatten_description(src.get("pattern_description"))
        src["_seq_no"] = resp.get("_seq_no")
        src["_primary_term"] = resp.get("_primary_term")
        return src

    def existing_event_ids(self, event_ids: list[str]) -> set[str]:
        """Return the subset of event_ids that actually exist in drift_events.

        Referential-integrity check for hygiene: a source_event_id that is a
        well-formed UUID but has no backing drift_event is a hallucination the
        regex shape check cannot catch. One `terms` query per hygiene batch
        (cheap; the incremental scan keeps the batch small). De-duplicates the
        ids and queries only the distinct set.
        """
        distinct = sorted({e for e in event_ids if e})
        if not distinct:
            return set()
        body = {
            "query": {"terms": {"event_id": distinct}},
            "size": len(distinct),
            "_source": ["event_id"],
        }
        resp = self.client.request("POST", f"/{DRIFT_EVENTS_INDEX}/_search", body)
        hits = (resp.get("hits") or {}).get("hits") or []
        found = {(h.get("_source") or {}).get("event_id") for h in hits}
        found.discard(None)
        return found

    # --- writes (optimistic concurrency) ------------------------------------
    def write_pattern_occ(self, pattern: dict[str, Any]) -> dict[str, Any]:
        """Write a pattern back, asserting it has not changed since we read it.

        Requires `_seq_no` / `_primary_term` on the dict (from a prior read). A
        concurrent memory_synthesizer append bumps those tokens, so this write
        fails with 409 rather than silently clobbering the append — the caller
        treats 409 as "skip this op, retry next run" (conservative).

        Strips the internal tokens and any semantic_text echo before writing.
        Refreshes are wait_for so a subsequent count/search sees the change.
        """
        seq_no = pattern.get("_seq_no")
        primary_term = pattern.get("_primary_term")
        if seq_no is None or primary_term is None:
            raise ValueError("write_pattern_occ requires _seq_no/_primary_term from a prior read")

        pattern_id = pattern["pattern_id"]
        doc = {k: pattern[k] for k in (
            "pattern_id", "pattern_description", "pattern_type", "domain_tags",
            "source_event_ids", "support_count", "created_at", "last_updated_at",
        ) if k in pattern}
        # preserve record_source only if present (e.g. for tests); production
        # rows leave it unset (§2.3).
        if pattern.get("record_source") is not None:
            doc["record_source"] = pattern["record_source"]

        path = (
            f"/{self.index}/_doc/{pattern_id}"
            f"?if_seq_no={seq_no}&if_primary_term={primary_term}&refresh=wait_for"
        )
        return self.client.request("PUT", path, doc)

    def delete_by_id_occ(self, pattern_id: str, seq_no: int, primary_term: int) -> dict[str, Any]:
        """Delete one pattern with optimistic concurrency (used by merge: the
        losing row is deleted only if unchanged since recall)."""
        path = (
            f"/{self.index}/_doc/{pattern_id}"
            f"?if_seq_no={seq_no}&if_primary_term={primary_term}&refresh=wait_for"
        )
        return self.client.request("DELETE", path)

    def delete_by_ids(self, pattern_ids: list[str]) -> dict[str, Any]:
        """Targeted bulk delete by pattern_id (eviction). Mirrors the
        cleanup_probe_patterns.py delete_by_query pattern but keyed on explicit
        ids the eviction filter already selected, so it can never over-delete."""
        if not pattern_ids:
            return {"deleted": 0}
        body = {"query": {"terms": {"pattern_id": pattern_ids}}}
        return self.client.request(
            "POST", f"/{self.index}/_delete_by_query?refresh=true", body
        )
