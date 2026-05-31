"""Duplicate detection + merge for drift_patterns (C3 / D3, §3.6).

The dedup sub-task is the curator's one place that uses the LLM — but the LLM
only JUDGES; code RECALLS the candidates and code EXECUTES the merge. The flow
per scanned pattern:

  1. recall (deterministic, es_ops): keyword pre-filter + ELSER neighbor search
     -> candidate pairs;
  2. judge (one LLM call, llm_judge): "same phenomenon?" with conservative
     guardrails -> MergeDecision;
  3. if should_merge: build a merge PROPOSAL (pure code), validate it, then
     write via optimistic concurrency. The survivor absorbs the union of both
     rows' source_event_ids and the LLM's merged_description; the loser is
     deleted. A concurrent memory_synthesizer append bumps either row's
     seq_no/primary_term, so the OCC write/delete fails with 409 and the merge
     is skipped this run (retried next time) — it can never clobber an append.

De-dup is conservative by default (the LLM defaults to not-merging; see
llm_judge). Within one run we never merge the same row twice: once a pattern is
consumed as a loser, it is excluded from further pairing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .es_ops import PatternStore
from .llm_judge import MergeDecision, RawJudge, decide_merge


@dataclass
class MergeOutcome:
    survivor_id: str
    loser_id: str
    merged_description: str
    new_support_count: int
    rationale: str
    applied: bool                 # False if dry-run or OCC conflict
    note: str = ""


@dataclass
class DedupReport:
    pairs_judged: int = 0
    merges_applied: list[MergeOutcome] = field(default_factory=list)
    merges_proposed_only: list[MergeOutcome] = field(default_factory=list)  # dry-run
    conflicts: list[str] = field(default_factory=list)                      # OCC 409s
    declined: int = 0             # LLM said do-not-merge
    judgments_capped: bool = False  # True if max_judgments was hit (more pairs remain)


def _union_event_ids(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Order-preserving union of two rows' source_event_ids."""
    out: list[str] = []
    seen: set[str] = set()
    for src in (a, b):
        for eid in (src.get("source_event_ids") or []):
            if eid not in seen:
                seen.add(eid)
                out.append(eid)
    return out


def _build_merge_proposal(
    decision: MergeDecision,
    pat_x: dict[str, Any],
    pat_y: dict[str, Any],
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure code: produce (survivor_doc_to_write, loser_to_delete).

    survivor = the row whose pattern_id == decision.merge_into_pattern_id; it
    absorbs the union of source_event_ids, recomputes support_count, takes the
    merged_description, keeps its own created_at, refreshes last_updated_at, and
    carries forward its seq_no/primary_term for the OCC write. The loser carries
    its own tokens for the OCC delete.
    """
    if pat_x["pattern_id"] == decision.merge_into_pattern_id:
        survivor, loser = pat_x, pat_y
    else:
        survivor, loser = pat_y, pat_x

    union = _union_event_ids(survivor, loser)
    survivor_doc = dict(survivor)
    survivor_doc["pattern_description"] = decision.merged_description
    survivor_doc["source_event_ids"] = union
    survivor_doc["support_count"] = len(union)
    survivor_doc["last_updated_at"] = now_iso
    # domain_tags: union so the surviving row covers both (keeps retrieval recall)
    survivor_doc["domain_tags"] = sorted(
        set(survivor.get("domain_tags") or []) | set(loser.get("domain_tags") or [])
    )
    return survivor_doc, loser


def _noop_log(_msg: str) -> None:
    pass


def run_dedup(
    store: PatternStore,
    patterns: list[dict[str, Any]],
    now_iso: str,
    *,
    apply: bool,
    raw_judge: RawJudge | None = None,
    top_k: int = 5,
    max_judgments: int | None = 50,
    log: Callable[[str], None] = _noop_log,
) -> DedupReport:
    """Recall + judge + (optionally) merge across the scanned patterns.

    `apply=False` is dry-run: proposals are computed and reported but nothing is
    written. `raw_judge` is injected in tests (zero tokens).

    Bounded by design (the real-data lesson, 2026-05-31): the LLM judgment is the
    expensive, serial step, so this function:
      - judges each unordered pair AT MOST ONCE (a `judged_pairs` set of
        frozensets), so A's recall of B and B's recall of A don't double-judge;
      - stops after `max_judgments` LLM calls (None = unbounded), setting
        report.judgments_capped so the caller knows pairs remain for the next
        run. Without this cap a dense same-type index drove ~N*top_k serial
        Gemini calls and blew the Cloud Run task timeout.
      - emits progress via `log` (the curator wires a flushing print) so a long
        run is observable instead of silent.

    Returns a DedupReport summarizing every judged pair and applied/proposed
    merge.
    """
    report = DedupReport()
    consumed: set[str] = set()             # ids already merged away this run
    judged_pairs: set[frozenset] = set()   # unordered pairs already judged

    log(f"[dedup] start: {len(patterns)} patterns, top_k={top_k}, "
        f"max_judgments={max_judgments}")

    for pattern in patterns:
        pid = pattern.get("pattern_id")
        if not pid or pid in consumed:
            continue

        candidates = store.recall_duplicate_candidates(pattern, top_k=top_k)
        for cand in candidates:
            cid = cand.get("pattern_id")
            if not cid or cid in consumed or cid == pid:
                continue

            pair_key = frozenset((pid, cid))
            if pair_key in judged_pairs:
                continue  # A-B already judged when we visited the other side
            judged_pairs.add(pair_key)

            if max_judgments is not None and report.pairs_judged >= max_judgments:
                report.judgments_capped = True
                log(f"[dedup] hit max_judgments={max_judgments}; "
                    f"stopping (remaining pairs deferred to next run)")
                return report

            report.pairs_judged += 1
            if report.pairs_judged % 10 == 0:
                log(f"[dedup] judged {report.pairs_judged} pair(s), "
                    f"{len(report.merges_applied)} merged so far")

            decision = decide_merge(pattern, cand, raw_judge=raw_judge)
            if not decision.should_merge:
                report.declined += 1
                continue

            survivor_doc, loser = _build_merge_proposal(decision, pattern, cand, now_iso)
            outcome = MergeOutcome(
                survivor_id=survivor_doc["pattern_id"],
                loser_id=loser["pattern_id"],
                merged_description=survivor_doc["pattern_description"],
                new_support_count=survivor_doc["support_count"],
                rationale=decision.rationale,
                applied=False,
            )

            if not apply:
                report.merges_proposed_only.append(outcome)
                consumed.add(loser["pattern_id"])  # don't re-propose the loser
                # if the survivor is the scanned `pattern`, keep scanning its
                # other candidates; if it's the candidate, the scanned pattern
                # was merged away — stop pairing it.
                if survivor_doc["pattern_id"] != pid:
                    consumed.add(pid)
                    break
                continue

            applied, note = _apply_merge(store, survivor_doc, loser)
            outcome.applied = applied
            outcome.note = note
            if applied:
                report.merges_applied.append(outcome)
                consumed.add(loser["pattern_id"])
                if survivor_doc["pattern_id"] != pid:
                    consumed.add(pid)
                    break
            else:
                report.conflicts.append(
                    f"{survivor_doc['pattern_id']}<-{loser['pattern_id']}: {note}"
                )
                # OCC conflict: leave both rows untouched, try again next run.

    log(f"[dedup] done: {report.pairs_judged} judged, "
        f"{len(report.merges_applied)} merged, {report.declined} declined, "
        f"{len(report.conflicts)} conflict(s)")
    return report


def _apply_merge(
    store: PatternStore,
    survivor_doc: dict[str, Any],
    loser: dict[str, Any],
) -> tuple[bool, str]:
    """Execute a validated merge with optimistic concurrency.

    Order matters: write the survivor first (absorbs evidence), then delete the
    loser. If either OCC step 409s (a concurrent synthesizer append bumped the
    version), we abort and report — the index is left consistent because the
    survivor write is idempotent and the loser is only deleted after it
    succeeds. Returns (applied, note).
    """
    try:
        store.write_pattern_occ(survivor_doc)
    except RuntimeError as exc:
        if "409" in str(exc):
            return False, "survivor OCC conflict (concurrent append); skipped"
        raise

    loser_seq = loser.get("_seq_no")
    loser_term = loser.get("_primary_term")
    if loser_seq is None or loser_term is None:
        return False, "loser missing concurrency tokens; survivor written, loser NOT deleted"
    try:
        store.delete_by_id_occ(loser["pattern_id"], loser_seq, loser_term)
    except RuntimeError as exc:
        if "409" in str(exc):
            return False, "loser OCC conflict (concurrent append); survivor written, loser kept"
        raise
    return True, "merged"
