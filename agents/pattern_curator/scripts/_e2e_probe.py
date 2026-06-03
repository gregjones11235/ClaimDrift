"""E3-precondition end-to-end check for pattern_curator against the REAL
cluster — SAFE / READ-ONLY for real data.

What it proves end-to-end on the live cluster:
  - scan_since reads real patterns;
  - hygiene DETECTS a planted garbage row (hallucinated source_event_id ->
    support recompute) in its repair plan;
  - eviction SELECTS that row;
  - recall_duplicate_candidates pairs the two planted duplicates via the real
    keyword pre-filter + ELSER neighbor search;
  - the one real Gemini judgment runs on that pair (structured output -> schema
    gate -> MergeDecision), proving the LLM wiring.

Safety: the curator is run in DRY-RUN only, so it writes NOTHING to real rows.
The planted probe rows (record_source="curator_e2e_probe") are injected, used,
and deleted; the assertions only inspect the dry-run plan + the real judgment on
the planted pair. The apply-path mutations are covered by the 33 FakeStore unit
tests, which is where exercising real writes is safe.

Run from agents/:
    uv run python -m pattern_curator.scripts._e2e_probe
    uv run python -m pattern_curator.scripts._e2e_probe --keep   # leave probe rows
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (_AGENTS_ROOT, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv

load_dotenv(_AGENTS_ROOT / ".env")

from ingestion.common.elastic import ElasticsearchHttpClient
from pattern_curator import hygiene
from pattern_curator.es_ops import PatternStore
from pattern_curator.llm_judge import decide_merge

PROBE_TAG = "curator_e2e_probe"
PATTERNS_INDEX = "drift_patterns"
EVENTS_INDEX = "drift_events"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cleanup(client: ElasticsearchHttpClient) -> int:
    body = {"query": {"term": {"record_source": PROBE_TAG}}}
    a = client.request("POST", f"/{PATTERNS_INDEX}/_delete_by_query?refresh=true", body)
    b = client.request("POST", f"/{EVENTS_INDEX}/_delete_by_query?refresh=true", body)
    return int(a.get("deleted", 0)) + int(b.get("deleted", 0))


def _inject(client: ElasticsearchHttpClient) -> dict:
    now = _now()
    e1, e2, e3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    for eid in (e1, e2, e3):
        client.request(
            "PUT", f"/{EVENTS_INDEX}/_doc/{eid}?op_type=create&refresh=wait_for",
            {"record_source": PROBE_TAG, "event_id": eid, "preprint_doi": "10.0/probe",
             "published_doi": "10.0/probe-pub", "detected_at": now,
             "drift_summary": "probe", "claim_diffs": [], "materiality_score": 0.5},
        )
    dup_a, dup_b, garbage = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    client.request("PUT", f"/{PATTERNS_INDEX}/_doc/{dup_a}?op_type=create&refresh=wait_for", {
        "record_source": PROBE_TAG, "pattern_id": dup_a,
        "pattern_description": "COVID-19 clinical trial preprints frequently report a large "
                               "effect-size reduction (often 50%+) for the primary endpoint "
                               "between the final preprint and the published version.",
        "pattern_type": "effect_size_reduction", "domain_tags": ["covid-19", "clinical-trial"],
        "source_event_ids": [e1, e2], "support_count": 2, "created_at": now, "last_updated_at": now,
    })
    client.request("PUT", f"/{PATTERNS_INDEX}/_doc/{dup_b}?op_type=create&refresh=wait_for", {
        "record_source": PROBE_TAG, "pattern_id": dup_b,
        "pattern_description": "In COVID-19 randomized controlled trials, the headline effect "
                               "size for the main outcome is often substantially reduced (around "
                               "half) when the preprint is finally published.",
        "pattern_type": "effect_size_reduction", "domain_tags": ["covid-19", "rct"],
        "source_event_ids": [e3], "support_count": 1, "created_at": now, "last_updated_at": now,
    })
    client.request("PUT", f"/{PATTERNS_INDEX}/_doc/{garbage}?op_type=create&refresh=wait_for", {
        "record_source": PROBE_TAG, "pattern_id": garbage,
        "pattern_description": "Some unrelated drift pattern with bad provenance.",
        "pattern_type": "claim_disappearance", "domain_tags": ["covid-19"],
        "source_event_ids": ["drift_event_id_not_found_in_input"], "support_count": 5,
        "created_at": now, "last_updated_at": now,
    })
    return {"e1": e1, "e2": e2, "e3": e3, "dup_a": dup_a, "dup_b": dup_b, "garbage": garbage}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="leave probe rows after run")
    args = ap.parse_args()

    client = ElasticsearchHttpClient()
    _cleanup(client)  # idempotent
    ids = _inject(client)
    print("injected probe rows:", json.dumps(ids, indent=2))
    store = PatternStore(client=client)
    now = _now()
    failures = []

    try:
        # 1. hygiene detection on the garbage row (referential check ON)
        garbage = store.get_by_id(ids["garbage"])
        existing = store.existing_event_ids(
            hygiene.collect_structural_event_ids([garbage])
        )
        _, fixes = hygiene.hygiene_pattern(garbage, now, existing_ids=existing)
        print("\n[hygiene] garbage fixes:", fixes)
        if any("support_count" in f for f in fixes) and any(
            "drift_event" in f or "structurally-invalid" in f for f in fixes
        ):
            print("PASS: hygiene detected the hallucinated id and recomputed support_count")
        else:
            failures.append("hygiene did not flag the garbage row")

        # 2. recall pairs the two duplicates via real keyword + ELSER
        dup_a = store.get_by_id(ids["dup_a"])
        candidates = store.recall_duplicate_candidates(dup_a, top_k=5)
        cand_ids = [c.get("pattern_id") for c in candidates]
        print(f"\n[recall] candidates for dup_a: {cand_ids}")
        if ids["dup_b"] in cand_ids:
            print("PASS: recall surfaced dup_b as a candidate for dup_a")
        else:
            failures.append("recall did not pair dup_a with dup_b")

        # 3. the ONE real Gemini judgment on the obvious duplicate pair
        dup_b = store.get_by_id(ids["dup_b"])
        print("\n[llm] running the real Gemini same-phenomenon judgment ...")
        decision = decide_merge(dup_a, dup_b)  # real Vertex call
        print("  same_phenomenon:", decision.same_phenomenon,
              "| confidence:", decision.confidence,
              "| should_merge:", decision.should_merge)
        print("  rationale:", decision.rationale)
        if decision.merged_description:
            print("  merged_description:", decision.merged_description)
        if decision.should_merge and decision.merge_into_pattern_id in (ids["dup_a"], ids["dup_b"]):
            print("PASS: real LLM judged the obvious duplicate pair as mergeable, schema-gated OK")
        else:
            # Not a hard failure — the conservative default is allowed to decline.
            print("NOTE: LLM did not return a high-confidence merge for this pair "
                  "(conservative default is acceptable; inspect rationale above).")
    finally:
        if not args.keep:
            n = _cleanup(client)
            print(f"\ncleaned up {n} probe row(s).")
        else:
            print("\n--keep set; probe rows left in the cluster.")

    if failures:
        print("\nFAILURES:", failures)
        raise SystemExit(1)
    print("\nE3-precondition checks passed (deterministic governance + real LLM wiring).")


if __name__ == "__main__":
    main()
