"""In-memory fakes for curator tests — a FakeStore implementing the subset of
PatternStore the curator uses, with seq_no/primary_term bookkeeping so the
optimistic-concurrency paths are exercised for real."""
from __future__ import annotations

from typing import Any


class FakeStore:
    """Minimal stand-in for es_ops.PatternStore.

    Holds patterns in a dict keyed by pattern_id, each with a `_seq_no` that
    bumps on every write (so a stale OCC token causes a simulated 409). Also
    holds a set of existing drift_event ids for the referential check.
    """

    def __init__(self, patterns: list[dict[str, Any]], existing_event_ids: set[str] | None = None,
                 recall_map: dict[str, list[str]] | None = None):
        self.patterns: dict[str, dict[str, Any]] = {}
        for p in patterns:
            row = dict(p)
            row.setdefault("_seq_no", 0)
            row.setdefault("_primary_term", 1)
            self.patterns[row["pattern_id"]] = row
        self._existing = existing_event_ids if existing_event_ids is not None else set()
        # recall_map: pattern_id -> list of candidate pattern_ids to return.
        self._recall_map = recall_map or {}
        self.deleted: list[str] = []

    # --- reads ---
    def scan_since(self, watermark_iso, size=500):
        rows = sorted(self.patterns.values(), key=lambda r: r.get("last_updated_at") or "")
        if watermark_iso:
            rows = [r for r in rows if (r.get("last_updated_at") or "") >= watermark_iso]
        return [dict(r) for r in rows]

    def existing_event_ids(self, event_ids):
        return {e for e in event_ids if e in self._existing}

    def recall_duplicate_candidates(self, pattern, top_k=5):
        ids = self._recall_map.get(pattern["pattern_id"], [])
        return [dict(self.patterns[i]) for i in ids if i in self.patterns][:top_k]

    def get_by_id(self, pattern_id):
        row = self.patterns.get(pattern_id)
        return dict(row) if row else None

    # --- writes (OCC) ---
    def write_pattern_occ(self, pattern):
        pid = pattern["pattern_id"]
        cur = self.patterns.get(pid)
        seq = pattern.get("_seq_no")
        term = pattern.get("_primary_term")
        if seq is None or term is None:
            raise ValueError("write_pattern_occ requires _seq_no/_primary_term")
        if cur is not None and cur["_seq_no"] != seq:
            raise RuntimeError("Elasticsearch PUT failed: HTTP 409 version_conflict")
        stored = {k: v for k, v in pattern.items() if not k.startswith("_")}
        stored["_seq_no"] = (cur["_seq_no"] + 1) if cur else 0
        stored["_primary_term"] = 1
        self.patterns[pid] = stored
        return {"result": "updated", "_seq_no": stored["_seq_no"],
                "_primary_term": stored["_primary_term"]}

    def delete_by_id_occ(self, pattern_id, seq_no, primary_term):
        cur = self.patterns.get(pattern_id)
        if cur is None:
            return {"result": "not_found"}
        if cur["_seq_no"] != seq_no:
            raise RuntimeError("Elasticsearch DELETE failed: HTTP 409 version_conflict")
        del self.patterns[pattern_id]
        self.deleted.append(pattern_id)
        return {"result": "deleted"}

    def delete_by_ids(self, pattern_ids):
        n = 0
        for pid in pattern_ids:
            if pid in self.patterns:
                del self.patterns[pid]
                self.deleted.append(pid)
                n += 1
        return {"deleted": n}
