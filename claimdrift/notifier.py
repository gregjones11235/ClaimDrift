"""notifier (§3.8, P0.7): a pure transformation node with a hard gate.

Gate: a notification is created only for an affected citation that is "superseded" or "indirect" (the relaying paper is
the one that needs telling) and has not been rejected in review; the drift event must not be rejected either. There is no
approval step before sending (decision 2026-10-01): all mail goes to the test inbox, so a wrong notice costs nothing.
The citation worker calls notify_pending() right after a run; `python -m claimdrift notify` re-runs it by hand.

Content: drafted by flash (decision 2026-10-02, as in the old system's notifier agent): addressed to the first author,
neutral, both versions quoted verbatim, the authors decide. The facts come only from the affected citation and the drift
event; a draft that drops either quoted version or fails falls back to the fixed template (template_draft).
Recipient: this project is not a commercial product, so EVERY message goes to NOTIFY_OVERRIDE_EMAIL (the project's test
inbox), unconditionally. notification_log still records which paper/authors the notice was about.
Delivery (send_mail): Gmail API with the sender's OAuth token (GMAIL_TOKEN_FILE, created once by
claimdrift/scripts/gmail_oauth_local.py); without it the notice is stored as a draft ("drafted", delivery "draft_only"). send_mail is also used by the Playground, which mails the address its user typed in.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from email.mime.text import MIMEText

from . import config, es, llm, store

log = logging.getLogger("claimdrift.notifier")
AC, NL, EV = config.INDICES["affected_citations"], config.INDICES["notification_log"], config.INDICES["drift_events"]
NOTIFY_CLASSES = ("superseded", "indirect")


class GateError(RuntimeError):
    pass


def check_gate(ac: dict, event: dict | None) -> None:
    if ac.get("cites") not in NOTIFY_CLASSES:
        raise GateError(f"cites={ac.get('cites')!r}: only superseded/indirect citations are notified")
    if ac.get("review_status") == "rejected":
        raise GateError("the citation was rejected in review")
    if event is None:
        raise GateError("drift event not found")
    if event.get("review_status") == "rejected":
        raise GateError("the drift event itself was rejected in review")


ROLE_NOTE = {
    "model_input": "Your paper appears to use this value as a model input or parameter, so results that depend on it may "
                   "shift with the revised value.",
    "reported_as_fact": "Your paper reports this value as a finding.",
    "background": "Your paper mentions this value as background.",
}


def _greeting(ac: dict) -> str:
    """'Dear Dr Smith and co-authors,' from the first listed author (Europe PMC style 'Smith J'); generic otherwise."""
    authors = [a.get("name") for a in ac.get("citing_paper_authors") or [] if a.get("name")]
    if not authors:
        return "Dear authors,"
    surname = authors[0].split()[0].strip(",.")
    return f"Dear Dr {surname}{' and co-authors' if len(authors) > 1 else ''},"


DRAFT_SYSTEM = """You draft a short notification email to the authors of a paper that cites a preprint value which was later
revised in the preprint's published version. You are given the facts as JSON; use ONLY these facts, never invent any.

Requirements (non-negotiable):
- Address the first author by name, e.g. "Dear Dr Smith and co-authors," (use the `greeting` field as given).
- Neutral and informational: no lecturing, no blame, no claim that their paper is wrong.
- Quote the preprint's first-version text and the published-version text VERBATIM, each on its own indented line.
- Quote the passage from their paper; if `passage_verified` is false, say it was identified automatically.
- Explain briefly what changed (from `what_changed`) and, if `use_in_paper` is given, why it may matter for how their paper
  uses the value. For cites = "indirect", explain that their paper credits the value to `relayed_by`, which took it from
  the preprint.
- Say clearly that this is an automated notice, that the comparison may be imperfect, and that the authors decide whether
  anything needs updating; no reply is needed.
- 150-300 words. Plain text, no markdown. Sign off as "ClaimDrift".
- Subject: specific, polite, under 120 characters, starting with "[ClaimDrift]".

Return ONLY JSON: {"subject": "...", "body": "..."}"""

USE_IN_PAPER = {"model_input": "used as a model input or parameter", "reported_as_fact": "reported as a finding",
                "background": "mentioned as background"}


def _texts(ac: dict, event: dict) -> tuple[dict, str | None, str | None]:
    diff = {}
    idx = ac.get("claim_diff_idx")
    if isinstance(idx, int) and 0 <= idx < len(event.get("claim_diffs") or []):
        diff = event["claim_diffs"][idx]
    old, new = diff.get("preprint_text"), diff.get("published_text")
    if not diff:  # case-bank targets are not tied to one claim_diff (claim_diff_idx None): use the target's own texts
        from .citations.targets import case_targets
        drift = (case_targets().get(event.get("paper_id")) or {}).get("drift") or {}
        old, new = drift.get("preprint_v1_claim"), drift.get("current_claim")
    return diff, old, new


def draft(ac: dict, event: dict, backend=None) -> tuple[str, str]:
    """LLM draft (flash); falls back to template_draft when the call fails or the draft drops a quoted version."""
    diff, old, new = _texts(ac, event)
    facts = {"greeting": _greeting(ac), "citing_paper_title": ac.get("citing_paper_title"),
             "citing_paper_link": f"https://doi.org/{ac['citing_paper_doi']}" if ac.get("citing_paper_doi") else ac.get("work_id"),
             "cites": ac.get("cites"), "relayed_by": ac.get("relayed_by"),
             "preprint": {"first_author": event.get("first_author"), "title": event.get("preprint_title"),
                          "link": f"https://doi.org/{event.get('preprint_doi')}"},
             "published_version_link": f"https://doi.org/{event.get('published_doi')}",
             "passage_in_their_paper": ac.get("sentence") or ac.get("citation_context") or "",
             "passage_verified": bool(ac.get("sentence_verified")),
             "preprint_first_version_text": old or "", "published_version_text": new or "(no counterpart in the published version)",
             "what_changed": diff.get("change_description") or event.get("drift_summary") or "",
             "use_in_paper": USE_IN_PAPER.get(ac.get("role") or "")}
    try:
        backend = backend or llm.flash()
        r = backend.chat([{"role": "system", "content": DRAFT_SYSTEM},
                          {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}])
        out = llm.extract_json(r["content"]) or {}
        subject, body = str(out.get("subject") or "").strip(), str(out.get("body") or "").strip()
        squash = lambda x: re.sub(r"\W+", "", (x or "").lower())  # noqa: E731
        if subject and body and (not old or squash(old) in squash(body)) and (not new or squash(new) in squash(body)):
            return subject[:200], body
        log.warning("LLM draft for %s dropped a quoted version; using the template", ac.get("affected_citation_id"))
    except Exception as e:  # noqa: BLE001
        log.warning("LLM draft for %s failed (%s); using the template", ac.get("affected_citation_id"), e)
    return template_draft(ac, event)


def draft_via_agent(ac: dict, event: dict) -> tuple[str, str]:
    """draft() on the notifier engine when Agent Engine is configured, else in-process; the template if the engine fails.
    The ES documents are copied through json so the request carries plain JSON."""
    from . import engines
    if not engines.configured():
        return draft(ac, event)
    try:
        r = engines.call_remote("notifier", json.loads(json.dumps({"ac": ac, "event": event}, default=str)))
        return r["subject"], r["body"]
    except Exception as e:  # noqa: BLE001
        log.warning("notifier engine failed for %s (%s); using the template", ac.get("affected_citation_id"), e)
        return template_draft(ac, event)


def template_draft(ac: dict, event: dict) -> tuple[str, str]:
    """Deterministic, factual notice (fallback): greeting, which sentence relies on which revised value, old vs new text,
    what changed, how the value is used, and a neutral close that leaves the judgement to the authors."""
    diff = {}
    idx = ac.get("claim_diff_idx")
    if isinstance(idx, int) and 0 <= idx < len(event.get("claim_diffs") or []):
        diff = event["claim_diffs"][idx]
    old, new = diff.get("preprint_text"), diff.get("published_text")
    if not diff:  # case-bank targets are not tied to one claim_diff (claim_diff_idx None): use the target's own texts
        from .citations.targets import case_targets
        drift = (case_targets().get(event.get("paper_id")) or {}).get("drift") or {}
        old, new = drift.get("preprint_v1_claim"), drift.get("current_claim")
    old = old or "(see the preprint's first version)"
    new = new or "(no counterpart in the published version)"
    title = ac.get("citing_paper_title") or ac.get("work_id")
    ref = ac.get("citing_paper_doi") and f"https://doi.org/{ac['citing_paper_doi']}" or ac.get("work_id")
    target = f"{event.get('first_author')} et al." if event.get("first_author") else "a preprint"
    pre, pub = event.get("preprint_doi"), event.get("published_doi")
    if ac.get("cites") == "indirect":
        via = ac.get("relayed_by") or "another paper"
        subject = f"[ClaimDrift] A value your paper cites via {via} was revised by its original authors"
        lead = [f"Your paper \"{title}\" ({ref}) uses a value that it credits to {via}. That value comes originally from the "
                f"preprint by {target} (https://doi.org/{pre}), and the authors revised it when the work was published "
                f"(https://doi.org/{pub})."]
    else:
        subject = f"[ClaimDrift] A value your paper cites from {target} was revised in the published version"
        lead = [f"Your paper \"{title}\" ({ref}) cites the preprint by {target} (https://doi.org/{pre}). One value it relies on "
                f"was revised between the preprint's first version and the published version (https://doi.org/{pub})."]
    sentence = ac.get("sentence") or ac.get("citation_context") or ""
    body = "\n".join([
        _greeting(ac), "",
        "I am writing on behalf of ClaimDrift, a project that tracks how findings change between a preprint's first "
        "version and its published version.", "",
        *lead, "",
        "The passage in your paper" + ("" if ac.get("sentence_verified") else " (as identified automatically)") + ":",
        f"    \"{sentence}\"", "",
        "Preprint, first version:",
        f"    {old}",
        "Published version:",
        f"    {new}", "",
        f"What changed: {diff.get('change_description') or event.get('drift_summary') or ''}",
        *([ROLE_NOTE[ac["role"]]] if ac.get("role") in ROLE_NOTE else []), "",
        "This is an automated, factual notice, and the comparison may be imperfect. You are best placed to judge whether "
        "anything in your work needs updating; no reply is needed.", "",
        "Best regards,",
        "ClaimDrift",
    ])
    return subject, body


def mail_configured() -> bool:
    return bool(config.GMAIL_TOKEN_FILE)


def send_mail(to: str, subject: str, body: str) -> str:
    """Send one plain-text message; returns a message id. Raises when no sender is configured."""
    if config.GMAIL_TOKEN_FILE:
        return _gmail_send(to, subject, body)
    raise RuntimeError("no Gmail sender configured: run claimdrift/scripts/gmail_oauth_local.py once (sets GMAIL_TOKEN_FILE)")


def _gmail_send(to: str, subject: str, body: str) -> str:
    from google.oauth2.credentials import Credentials  # lazy: optional dependency
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_info(json.loads(open(config.GMAIL_TOKEN_FILE, encoding="utf-8").read()),
                                                  ["https://www.googleapis.com/auth/gmail.send"])
    msg = MIMEText(body)
    msg["to"], msg["subject"] = to, subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return build("gmail", "v1", credentials=creds, cache_discovery=False).users().messages().send(userId="me", body={"raw": raw}).execute()["id"]


def notify(ac_id: str, send: bool = True) -> dict:
    ac = es.source(AC, ac_id)
    if ac is None:
        raise KeyError(ac_id)
    event = store.get_event(ac["drift_event_id"])
    check_gate(ac, event)
    if es.get(NL, ac_id):
        return {"skipped": "already notified", "affected_citation_id": ac_id}
    subject, body = draft_via_agent(ac, event)
    doc = {"record_source": "pipeline", "affected_citation_id": ac_id, "drift_event_id": ac["drift_event_id"],
           "target_id": ac.get("target_id"), "citing_work_id": ac.get("work_id"), "cites": ac.get("cites"),
           "intended_for": {"citing_paper_title": ac.get("citing_paper_title"), "citing_paper_doi": ac.get("citing_paper_doi"),
                            "authors": [a.get("name") for a in ac.get("citing_paper_authors") or []], "relayed_by": ac.get("relayed_by")},
           "recipient_email": config.NOTIFY_OVERRIDE_EMAIL, "subject": subject, "body": body,
           "reasoning_trace": ac.get("reason"), "reviewer": ac.get("reviewer"), "approved_at": ac.get("reviewed_at"),
           "drafted_at": store.now(), "sent_at": None, "status": "drafted", "delivery": "draft_only", "error_message": None}
    if send and mail_configured():
        try:
            send_mail(config.NOTIFY_OVERRIDE_EMAIL, subject, body)
            doc.update({"status": "sent", "sent_at": store.now(), "delivery": "gmail"})
        except Exception as e:  # noqa: BLE001
            doc.update({"status": "failed", "error_message": f"{type(e).__name__}: {e}"[:1000]})
    es.put(NL, ac_id, doc)
    return doc


def notify_pending(send: bool = True, event_id: str | None = None) -> list[dict]:
    """Notify every superseded/indirect citation (optionally of one event) not rejected and not yet in notification_log."""
    filters = [{"terms": {"cites": list(NOTIFY_CLASSES)}}]
    if event_id:
        filters.append({"term": {"drift_event_id": event_id}})
    q = {"bool": {"filter": filters, "must_not": [{"term": {"review_status": "rejected"}}]}}
    out = []
    for ac in es.hits(AC, q, size=1000):
        try:
            out.append(notify(ac["_id"], send=send))
        except GateError as e:
            out.append({"affected_citation_id": ac["_id"], "skipped": str(e)})
    return out
