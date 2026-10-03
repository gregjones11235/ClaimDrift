"""Citation targets: which superseded value of a drift event the citation analysis should look for (§3.5 trigger:
"the event states an explicit old conclusion / old value").

One target per claim_diff that carries a superseded value. Case-bank papers use the hand-written targets in
case_targets.json (same terms as the experiments, so recall is comparable); other papers are derived automatically from
the claim_diff text. Diffs whose root cause says the result itself did not change (wording_only, reporting_choice) are
skipped: a paper quoting the preprint value there is not relying on anything that was revised.

Changes without a number (e.g. a conclusion that changed its nature) produce no target: the string pre-screen cannot
find them and the semantic channel is still undecided (P2). This is a known weak spot, reported as such.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .terms import old_value_terms, term_regex

SKIP_ROOT_CAUSES = {"wording_only", "reporting_choice"}


@lru_cache(maxsize=1)
def case_targets() -> dict:
    return json.loads((Path(__file__).parent / "case_targets.json").read_text(encoding="utf-8"))["targets"]


def _match_diff(diffs: list[dict], terms: list[str]) -> int | None:
    """Index of the claim_diff that states the superseded value. Second pass on the bare number: the preprint text may
    put the unit elsewhere ("5.8 (4.6 - 7.9, 95% CI) days" for the term "5.8 days")."""
    blobs = [" ".join(str(d.get(k) or "") for k in ("preprint_text", "change_description")) for d in diffs]
    for pats in ([term_regex(t) for t in terms],
                 [term_regex(m.group(0)) for t in terms for m in [re.match(r"\s*\d+(?:[.,·]\d+)*", t)] if m]):
        for i, blob in enumerate(blobs):
            if any(p.search(blob) for p in pats):
                return i
    return None


def build_targets(event: dict, paper_id: str, title: str = "", first_author: str = "") -> list[dict]:
    diffs = event.get("claim_diffs") or []
    base = {"drift_event_id": event["event_id"], "paper_id": paper_id,
            "preprint_doi": event.get("preprint_doi"), "published_doi": event.get("published_doi")}
    if paper_id in case_targets():
        t = case_targets()[paper_id]
        idx = _match_diff(diffs, t["terms"])
        return [base | {"target_id": f"{event['event_id']}::{idx if idx is not None else 'case'}", "claim_diff_idx": idx,
                        "first_author": t["first_author"], "title_fragment": t["title_fragment"],
                        "preprint_doi": t["preprint_doi"], "published_doi": t["published_doi"],
                        "drift": t["drift"], "terms": list(t["terms"]), "target_source": "case_bank"}]
    out = []
    for i, d in enumerate(diffs):
        if d.get("root_cause") in SKIP_ROOT_CAUSES:
            continue
        terms = old_value_terms(d.get("preprint_text"), d.get("published_text"))
        if not terms:
            continue
        out.append(base | {"target_id": f"{event['event_id']}::{i}", "claim_diff_idx": i, "first_author": first_author,
                           "title_fragment": (title or "")[:60],
                           "drift": {"id": f"D{i}", "preprint_v1_claim": d.get("preprint_text") or "",
                                     "current_claim": d.get("published_text") or "(no counterpart in the published paper)"},
                           "terms": terms, "target_source": "derived"})
    return out
