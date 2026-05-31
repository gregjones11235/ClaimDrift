"""
Elastic retrieval helpers for the memory loop.

Both Drift Analyzer (contracts.md §3.2.1) and Memory Synthesizer (§3.5.1)
need to hybrid-search the `drift_patterns` index. This module owns that
single shared implementation, exposed as a plain Python function so it
can be passed directly to `LlmAgent(tools=[...])` as an ADK function
tool — the function's signature, type hints, and docstring become the
tool's schema that Gemini sees.

Transport: reuses `ingestion.common.elastic.ElasticsearchHttpClient`
(bare urllib + ApiKey auth, same client B uses everywhere else). No
extra deps.

Query shape: `retriever.rrf` over BM25 and ELSER semantic_text on
`pattern_description` — verified working on serverless 9.5 (see
contracts.md changelog 2026-05-22). RRF scores are rank-based and live
in roughly 0.01–0.05; do NOT use the §3.2 cosine-style 0.7 threshold
without probing first (see scripts/probe_rrf_scores.py).

Read target: the `drift_patterns_read` alias, not the concrete index, so
the offline pattern_curator can blue-green rebuild and atomically swap the
underlying index without the live read path noticing (contracts.md §2.2.5,
memory_loop_v2_design.md B.2). Writes are unaffected — they still target the
concrete `drift_patterns` index (see _shared/elastic_write.py). Falls back to
the concrete index if the alias is not yet created (_read_target).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# agents/_shared/ -> agents/ -> repo root, so ingestion/ is importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

# Load .env once at import time so this module works both inside `adk web`
# (which loads .env itself) and from standalone scripts under agents/scripts/.
load_dotenv(_REPO_ROOT / "agents" / ".env")

from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

# Writes target the concrete index; real-time READS go through the alias so the
# pattern_curator can blue-green rebuild and atomically swap the index under the
# live read path (contracts.md §2.2.5, memory_loop_v2_design.md B.2). The alias
# points at `drift_patterns` until the curator swaps it to `drift_patterns_v2`;
# managed by elastic/scripts/manage_pattern_alias.py. Falls back to the concrete
# index name if the alias has not been created yet (init not run), so retrieval
# never hard-fails on a fresh cluster — _resolve_read_target handles that.
DRIFT_PATTERNS_INDEX = "drift_patterns"
DRIFT_PATTERNS_READ_ALIAS = "drift_patterns_read"

# RRF rank_constant — Elastic's default is 60; B's verified query uses 60.
# rank_window_size scales with top_k so we don't truncate candidates before
# the rank fusion can do its job.
_RRF_RANK_CONSTANT = 60


def _read_target(client: ElasticsearchHttpClient) -> str:
    """Resolve the index/alias the real-time read path should query.

    Prefer the `drift_patterns_read` alias so curator blue-green swaps are
    transparent to retrieval. Fall back to the concrete index when the alias
    has not been created yet (a fresh cluster before
    `manage_pattern_alias.py init --apply`), so this change can never turn a
    working retrieval path into a hard 404. The check is a cheap HEAD-style
    GET /_alias/<name>; ES caches alias metadata so this is negligible next to
    the _search itself.
    """
    try:
        client.request("GET", f"/_alias/{DRIFT_PATTERNS_READ_ALIAS}")
        return DRIFT_PATTERNS_READ_ALIAS
    except RuntimeError as exc:
        if "404" in str(exc):
            return DRIFT_PATTERNS_INDEX
        raise


def _build_rrf_query(
    query_text: str,
    top_k: int,
    exclude_demo_seed: bool,
) -> dict[str, Any]:
    """Build the retriever.rrf body for /drift_patterns/_search.

    Two sub-retrievers are fused:
      - standard BM25 match on pattern_description
      - standard semantic match on pattern_description (ELSER, served by
        the .elser-2-elastic inference endpoint per the 2026-05-22 setup)

    `exclude_demo_seed` filters out demo-seed documents using the
    `record_source` keyword field that B added on the live cluster
    (`record_source == "demo_seed"` for seed docs; unset for real
    puller-ingested docs — confirmed by Jeremy 2026-05-22 on Discord).
    The `term` clause inside `must_not` naturally leaves unset values
    in the result set, which is what we want.
    """
    bm25_retriever: dict[str, Any] = {
        "standard": {
            "query": {
                "match": {
                    "pattern_description": {"query": query_text},
                },
            },
        },
    }
    semantic_retriever: dict[str, Any] = {
        "standard": {
            "query": {
                "semantic": {
                    "field": "pattern_description",
                    "query": query_text,
                },
            },
        },
    }

    if exclude_demo_seed:
        demo_filter = {
            "bool": {
                "must_not": [{"term": {"record_source": "demo_seed"}}],
            }
        }
        bm25_retriever["standard"]["query"] = {
            "bool": {
                "must": [bm25_retriever["standard"]["query"]],
                "filter": [demo_filter],
            }
        }
        semantic_retriever["standard"]["query"] = {
            "bool": {
                "must": [semantic_retriever["standard"]["query"]],
                "filter": [demo_filter],
            }
        }

    return {
        "retriever": {
            "rrf": {
                "retrievers": [bm25_retriever, semantic_retriever],
                "rank_window_size": max(top_k * 4, 20),
                "rank_constant": _RRF_RANK_CONSTANT,
            },
        },
        "size": top_k,
        "_source": [
            "pattern_id",
            "pattern_description",
            "pattern_type",
            "domain_tags",
            "source_event_ids",
            "support_count",
            "created_at",
            "last_updated_at",
        ],
    }


def _shape_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Project an ES hit onto the retrieved_patterns schema in contracts.md
    §3.2.1 (Drift Analyzer input). Memory Synthesizer's
    existing_similar_patterns (§3.5.1) is a strict subset of this shape,
    so a single projection covers both call sites."""
    source = hit.get("_source", {}) or {}
    # semantic_text fields come back as objects in some serverless responses
    # ({"text": "...", "inference": {...}}); collapse to the raw text for
    # downstream prompt injection.
    description = source.get("pattern_description")
    if isinstance(description, dict):
        description = description.get("text") or description.get("value") or ""

    return {
        "pattern_id": source.get("pattern_id"),
        "pattern_description": description,
        "pattern_type": source.get("pattern_type"),
        "domain_tags": source.get("domain_tags") or [],
        "source_event_ids": source.get("source_event_ids") or [],
        "support_count": source.get("support_count"),
        "similarity_score": float(hit.get("_score", 0.0)),
    }


def search_drift_patterns(
    query_text: str,
    top_k: int = 3,
    min_score: float | None = None,
    exclude_demo_seed: bool = False,
) -> list[dict[str, Any]]:
    """Retrieve drift patterns by hybrid (BM25 + ELSER) search.

    This is the read side of ClaimDrift's memory loop. Drift Analyzer
    calls this before reasoning, using the preprint's abstract as the
    query (contracts.md §3.2.1). Memory Synthesizer calls this before
    deciding create-vs-update, using the new drift_event's drift_summary
    as the query (§3.5.1).

    Args:
      query_text: Natural-language text to retrieve against
        `pattern_description`. Drift Analyzer: pass the preprint
        abstract. Memory Synthesizer: pass the drift_event.drift_summary.
        Must be non-empty.
      top_k: Maximum number of patterns to return. Drift Analyzer uses
        3 (per §3.2.1). Memory Synthesizer uses 5 (per §3.5.1).
      min_score: If set, drop patterns whose similarity_score is below
        this floor. The score is an RRF score (rank-based, typically
        0.01–0.05 in this index), NOT a cosine similarity. Leave as None
        when probing; pass a calibrated value once probe_rrf_scores.py
        has been run against the real index.
      exclude_demo_seed: If True, exclude patterns whose
        `record_source` field is `"demo_seed"` (the marker B writes for
        hand-curated demo rows; real puller-ingested docs leave the
        field unset). Useful when demonstrating the real memory loop
        end-to-end and you don't want seed rows to dominate the
        results.

    Returns:
      A list of dicts matching the `retrieved_patterns` schema in
      contracts.md §3.2.1 — each dict has: pattern_id,
      pattern_description, pattern_type, domain_tags, source_event_ids,
      support_count, similarity_score. Empty list if nothing matches
      (Drift Analyzer is expected to handle empty per §3.2.1).

    Raises:
      RuntimeError: if the Elasticsearch request fails. Caller (the
        agent runtime) decides whether to retry; we do not swallow
        errors silently because a dead retrieval path silently degrades
        the memory loop into a no-op, which is exactly what we want to
        be loud about.
    """
    if not query_text or not query_text.strip():
        raise ValueError("search_drift_patterns: query_text must be non-empty.")
    if top_k <= 0:
        raise ValueError("search_drift_patterns: top_k must be positive.")

    client = ElasticsearchHttpClient()
    body = _build_rrf_query(query_text, top_k, exclude_demo_seed)
    response = client.request("POST", f"/{_read_target(client)}/_search", body)

    hits = (response.get("hits") or {}).get("hits") or []
    patterns = [_shape_hit(h) for h in hits]
    if min_score is not None:
        patterns = [p for p in patterns if p["similarity_score"] >= min_score]
    return patterns
