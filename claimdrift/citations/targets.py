"""Citation targets: which superseded value of a drift event the citation analysis should look for (§3.5 trigger:
"the event states an explicit old conclusion / old value").

One target per claim_diff that carries a superseded value; the search terms are always derived from the claim_diff
text (terms.old_value_terms), for case-bank papers too, so evaluation measures what production does. case_targets.json
only identifies the case-bank papers (first author, title fragment, DOIs, the claim pair for notices); its old
hand-written terms were removed on 2026-10-03. Diffs whose root cause says the result itself did not change
(wording_only, reporting_choice) are skipped: a paper quoting the preprint value there is not relying on anything that
was revised.

Changes without a number (e.g. a conclusion that changed its nature) produce no target: the string pre-screen cannot
find them and the semantic channel is still undecided (P2). This is a known weak spot, reported as such.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .terms import old_value_terms

SKIP_ROOT_CAUSES = {"wording_only", "reporting_choice"}


@lru_cache(maxsize=1)
def case_targets() -> dict:
    return json.loads((Path(__file__).parent / "case_targets.json").read_text(encoding="utf-8"))["targets"]


def build_targets(event: dict, paper_id: str, title: str = "", first_author: str = "") -> list[dict]:
    diffs = event.get("claim_diffs") or []
    base = {"drift_event_id": event["event_id"], "paper_id": paper_id,
            "preprint_doi": event.get("preprint_doi"), "published_doi": event.get("published_doi")}
    ident = case_targets().get(paper_id) or {}  # case-bank papers: known first author / title when the event lacks them
    first_author = first_author or ident.get("first_author") or ""
    title = title or ident.get("title_fragment") or ""
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
