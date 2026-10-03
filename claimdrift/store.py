"""Build and write ES documents. Every mapping is `dynamic: strict`, so model output is copied through explicit
whitelists -- an extra key invented by the model must never make a write fail."""
from __future__ import annotations

import datetime as dt
import re
import uuid

from . import config, es

DIFF_KEYS = ("diff_type", "preprint_text", "published_text", "change_description", "root_cause", "materiality",
             "rationale", "severity_tier", "evidence", "label_problems")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def event_id_for(preprint_doi: str, published_doi: str) -> str:
    """Deterministic: re-analysing the same pair overwrites its event instead of creating a duplicate."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"claimdrift:v1-vs-published:{preprint_doi.lower()}:{(published_doi or '').lower()}"))


def drift_event_doc(pair, analysis: dict, abstract_sev: dict, provenance: list[dict], verification: dict) -> dict:
    out = analysis["output"] or {}
    t = now()
    diffs = []
    for d in out.get("claim_diffs") or []:
        row = {k: d.get(k) for k in DIFF_KEYS}
        row["evidence"] = [{k: e.get(k) for k in ("doc_id", "section", "quote")} for e in d.get("evidence") or []]
        row["preprint_claim_id"] = row["published_claim_id"] = None
        diffs.append(row)
    route = analysis["route"]
    return {
        "record_source": "case_bank" if pair.source == "case_bank" else "pipeline",
        "event_id": event_id_for(pair.preprint_doi, pair.published_doi),
        "paper_id": pair.paper_id, "preprint_title": pair.title or None,
        "published_title": (pair.published.title or None) if "published" in pair.docs else None, "first_author": pair.first_author or None,
        "preprint_doi": pair.preprint_doi, "preprint_version_compared": "v1", "published_doi": pair.published_doi,
        "text_source": "jats",
        "detected_at": t, "analyzed_at": t,
        "drift_summary": out.get("drift_summary"),
        "claim_diffs": diffs,
        "materiality_score": out.get("materiality_score"),
        "analysis_mode": f"{route['route']}_{route['prompt_version']}",
        "analysis_route": route,
        "abstract_severity": abstract_sev,
        "fulltext_severity": {"tier": out.get("fulltext_tier"), "materiality_score": out.get("materiality_score"),
                              "prompt_version": route["prompt_version"], "model": config.MODEL_PRO},
        "preprint_versions": pair.versions, "preprint_withdrawn_versions": pair.withdrawn_versions,
        "provenance": provenance, "verification": verification,
        "usage": analysis.get("usage"),
    }


def claims_docs(event_id: str, pair, claims: dict[str, list[dict]]) -> list[tuple[str, dict]]:
    """Claims route (the default since 2026-10-02): the extracted claims, for inspection and display."""
    rows, t = [], now()
    for doc_id, cl in claims.items():
        for i, c in enumerate(cl):
            cid = f"{event_id}::{c.get('id') or f'{doc_id}{i}'}"
            rows.append((cid, {
                "record_source": "pipeline", "claim_id": cid, "drift_event_id": event_id, "doc_id": doc_id,
                "parent_doi": pair.preprint_doi if doc_id.startswith("preprint") else pair.published_doi,
                "parent_version": doc_id.replace("preprint_", "") if doc_id.startswith("preprint") else "published",
                "section": c.get("section"), "claim_idx": i, "text": c.get("text"), "kind": c.get("kind"),
                "claim_type": c.get("kind"), "support_sections": [s for s in c.get("support_sections") or [] if isinstance(s, str)],
                "numbers": c.get("numbers"), "model": config.MODEL_FLASH, "extracted_at": t}))
    return rows


def _authors(author_string: str | None) -> list[dict]:
    names = [a.strip() for a in re.split(r",\s*(?=[A-Z])", (author_string or "").rstrip(".")) if a.strip()]
    return [{"name": n, "orcid": None, "email": None} for n in names[:20]]  # e-mail is never extracted or stored


def _found_via(src: str | None) -> str:
    s = (src or "").split(":")[0]
    return {"prefetch": "prefetch", "search_more": "search_more", "chain": "chain", "semantic": "semantic"}.get(s, s or "prefetch")


def _priority(cites: str, role: str) -> str | None:
    if cites == "superseded":
        return "high" if role == "model_input" else "normal"
    if cites == "indirect":
        return "low"
    return None


def affected_citation_doc(event_id: str, target: dict, run_id: str, w: dict, review_status: str, review_reasons: list[str]) -> tuple[str, dict]:
    ac_id = f"{event_id}::{w['work_id']}"
    date = (w.get("date") or "") or None
    return ac_id, {
        "record_source": "pipeline", "affected_citation_id": ac_id, "drift_event_id": event_id,
        "target_id": target.get("target_id"), "claim_diff_idx": target.get("claim_diff_idx"), "run_id": run_id,
        "work_id": w["work_id"], "citing_paper_doi": w.get("doi"), "citing_paper_title": w.get("title"),
        "citing_paper_authors": _authors(w.get("authors")), "citing_paper_date": date if date and re.match(r"\d{4}", date) else None,
        "citing_paper_journal": w.get("journal"), "citation_context": w.get("sentence"),
        "cites": w["cites"], "role": w.get("role"), "sentence": w.get("sentence"), "sentence_verified": bool(w.get("sentence_verified")),
        "found_via": _found_via(w.get("found_via")), "found_via_detail": w.get("found_via"), "relayed_by": w.get("relayed_by") or None,
        "flags": w.get("flags") or [], "unclear_rule": w.get("unclear_rule") or None, "reason": w.get("reason"),
        "judged_by": w.get("judged_by"), "verification": w.get("verification"),
        "verdict_before_verification": w.get("verdict_before_verification"),
        "needs_notification": w["cites"] in ("superseded", "indirect"), "notify_priority": _priority(w["cites"], w.get("role") or ""),
        "review_status": review_status, "review_reasons": review_reasons, "reviewer": None, "reviewed_at": None,
        "review_note": None, "scored_at": now(),
    }


def write_event(doc: dict) -> None:
    es.put(config.INDICES["drift_events"], doc["event_id"], doc)


def get_event(event_id: str) -> dict | None:
    return es.source(config.INDICES["drift_events"], event_id)
