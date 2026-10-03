"""Human review (§3.7).

Event-level triggers (any one puts the drift event in the review queue):
  quote_unverified          an evidence quote failed the verbatim check
  severity_mismatch         abstract-level and full-text severity disagree badly (provisional rule below)
  withdrawn_version         the preprint has a withdrawn version
Citation-level trigger (affected_citations):
  unclear                   the citation analysis could not decide

Notifications are NOT gated by review (decision 2026-10-01): every message goes to the project's test inbox, so
superseded/indirect citations are notified as soon as the citation run finishes. Review can still reject a citation or
an event afterwards; rejected items are never notified.

review_status values: not_required | pending | approved | rejected.
"""
from __future__ import annotations

import datetime as dt

ABSTRACT_RANK = {"no_change": 0, "minor": 1, "major": 2}
FULLTEXT_RANK = {"minor": 0, "medium": 1, "significant": 2, "major": 3}
NOTIFY_CLASSES = {"superseded", "indirect"}


def severity_mismatch(abstract_class: str | None, fulltext_tier: str | None) -> bool:
    """PROVISIONAL mapping (the design leaves the exact rule open): flag only clear contradictions --
    the abstracts show no change while the full text is significant/major, or the abstracts show a major change while
    the full text is minor. Neighbouring levels are expected to differ (the two scales measure different things)."""
    a, f = ABSTRACT_RANK.get(abstract_class or ""), FULLTEXT_RANK.get(fulltext_tier or "")
    if a is None or f is None:
        return False
    return (a == 0 and f >= 2) or (a == 2 and f == 0)


def event_reasons(event: dict) -> list[str]:
    reasons = []
    ver = event.get("verification") or {}
    if ver.get("quotes_total") and ver.get("quotes_verified", 0) < ver["quotes_total"]:
        reasons.append("quote_unverified")
    if severity_mismatch((event.get("abstract_severity") or {}).get("class"), (event.get("fulltext_severity") or {}).get("tier")):
        reasons.append("severity_mismatch")
    if event.get("preprint_withdrawn_versions"):
        reasons.append("withdrawn_version")
    return reasons


def apply_event(event: dict, extra: list[str] | None = None) -> dict:
    """Set review fields on an event; never downgrades a decision a human already made."""
    reasons = sorted(set(event_reasons(event)) | set(extra or []) | set(event.get("review_reasons") or []))
    event["review_reasons"] = reasons
    if event.get("review_status") not in ("approved", "rejected"):
        event["review_status"] = "pending" if reasons else "not_required"
    event.setdefault("reviewer", None)
    event.setdefault("reviewed_at", None)
    return event


def citation_status(cites: str | None) -> tuple[str, list[str]]:
    if cites == "unclear":
        return "pending", ["unclear"]
    return "not_required", []


CITES = {"superseded", "current", "flagged_as_previous", "indirect", "not_relying", "unclear"}
ROLES = {"model_input", "reported_as_fact", "background", "unknown"}


def _keep_machine(doc: dict, key: str, value) -> None:
    """Remember the machine's value the first time a human overrides it (a later re-correction keeps the original)."""
    if not isinstance(doc.get("machine_verdict"), dict):
        doc["machine_verdict"] = {}
    doc["machine_verdict"].setdefault(key, value)


def correct_citation(doc: dict, corrected: dict) -> list[str]:
    if set(corrected) - {"cites", "role"}:
        raise ValueError("citations can be corrected only in cites / role")
    if corrected.get("cites") and corrected["cites"] not in CITES or corrected.get("role") and corrected["role"] not in ROLES:
        raise ValueError("unknown cites / role value")
    fields = []
    for k in ("cites", "role"):
        if corrected.get(k) and corrected[k] != doc.get(k):
            _keep_machine(doc, k, doc.get(k))
            doc[k] = corrected[k]
            fields.append(k)
    if "cites" in fields:
        doc["needs_notification"] = doc["cites"] in NOTIFY_CLASSES
    return fields


def correct_event(doc: dict, corrected: dict) -> list[str]:
    """Human classification of a drift event: abstract class, full-text tier, and per change root cause / tier."""
    from .prompts.drift_analyzer import ROOT_CAUSE_SET, TIERS
    if set(corrected) - {"abstract_class", "fulltext_tier", "claim_diffs"}:
        raise ValueError("events can be corrected only in abstract_class / fulltext_tier / claim_diffs")
    fields = []
    if corrected.get("abstract_class"):
        if corrected["abstract_class"] not in ABSTRACT_RANK:
            raise ValueError(f"abstract_class must be one of {sorted(ABSTRACT_RANK)}")
        cur = (doc.get("abstract_severity") or {}).get("class")
        if corrected["abstract_class"] != cur:
            _keep_machine(doc, "abstract_class", cur)
            doc.setdefault("abstract_severity", {})["class"] = corrected["abstract_class"]
            fields.append("abstract_class")
    if corrected.get("fulltext_tier"):
        if corrected["fulltext_tier"] not in TIERS:
            raise ValueError(f"fulltext_tier must be one of {TIERS}")
        cur = (doc.get("fulltext_severity") or {}).get("tier")
        if corrected["fulltext_tier"] != cur:
            _keep_machine(doc, "fulltext_tier", cur)
            doc.setdefault("fulltext_severity", {})["tier"] = corrected["fulltext_tier"]
            fields.append("fulltext_tier")
    diffs = doc.get("claim_diffs") or []
    for c in corrected.get("claim_diffs") or []:
        i = c.get("idx")
        if not isinstance(i, int) or not 0 <= i < len(diffs):
            raise ValueError(f"claim_diffs idx {i!r} out of range")
        for k, allowed in (("root_cause", ROOT_CAUSE_SET), ("severity_tier", set(TIERS))):
            v = c.get(k)
            if not v or v == diffs[i].get(k):
                continue
            if v not in allowed:
                raise ValueError(f"claim_diffs[{i}].{k} {v!r} not allowed")
            _keep_machine(doc, f"claim_diffs[{i}].{k}", diffs[i].get(k))
            diffs[i][k] = v
            fields.append(f"claim_diffs[{i}].{k}")
    return fields


def decide(doc: dict, decision: str, reviewer: str | None = None, note: str = "", corrected: dict | None = None,
           kind: str = "citation") -> dict:
    """Record a human decision. approved = the (possibly corrected) classification stands; rejected = the item is wrong
    as a whole (e.g. a false drift event, a paper that does not cite the target). `corrected` overrides the machine's
    classification with the human one; the machine's values are kept in `machine_verdict`. reviewer is optional."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    fields: list[str] = []
    if corrected:
        if decision == "rejected":
            raise ValueError("a rejected item is not corrected; approve it with the corrected classification instead")
        fields = correct_event(doc, corrected) if kind == "event" else correct_citation(doc, corrected)
    doc.update({"review_status": decision, "reviewer": (reviewer or "").strip() or None, "review_note": note or None,
                "reviewed_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    if fields:
        doc["corrected_fields"] = sorted(set(doc.get("corrected_fields") or []) | set(fields))
    return doc
