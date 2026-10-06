"""What the old value measures, for the pre-screen's quantity match (terms.quantity_sentences).

One flash call per target, before the pre-screen: from the superseded and the current claim, the measured property
(with the synonyms a citing paper would write next to the value) and the unit. The pre-screen then keeps a value written
without a unit only if the property is in the same sentence, and drops a value whose unit differs
("0–24 h" for an incubation range in days). The result is stored on the target (target["quantity"]) and in the run's
events, so every run shows what it screened for.
"""
from __future__ import annotations

from .. import llm
from .terms import canonical_unit

QUANTITY_SYSTEM = """A preprint claim was revised. Its old value is searched for in the full texts of citing papers.
Name the quantity the old value measures. Return JSON only:
{"property_terms": ["..."], "unit": "..."}
- property_terms: 2-6 lowercase words or short phrases a citing paper would write next to this value to say what it
  measures: the measured property and its common synonyms or abbreviations (e.g. "incubation period", "incubation time",
  "incubation"). Include the shortest distinctive form. No generic words ("patients", "median", "range", "mean", "days",
  "rate", "study").
- unit: the unit of the old value, as written or implied by the claim: one of days, hours, weeks, months, years, %, or
  "none" for a count or ratio without a unit."""


def quantity_context(target: dict, backend=None) -> dict | None:
    """-> {"property_terms": [...], "unit": "days"|...|"none"|None}, or None when the model call fails (the pre-screen
    then falls back to the value-only rule)."""
    d = target.get("drift") or {}
    user = (f"Superseded claim: {d.get('preprint_v1_claim') or ''}\nCurrent claim: {d.get('current_claim') or ''}\n"
            f"Old value searched for: {', '.join(target.get('terms') or [])}")
    try:
        backend = backend or llm.flash()
        r = backend.chat([{"role": "system", "content": QUANTITY_SYSTEM}, {"role": "user", "content": user}])
    except Exception:  # noqa: BLE001
        return None
    out = llm.extract_json(r.get("content") or "") or {}
    words = [str(w).strip().lower() for w in out.get("property_terms") or [] if str(w).strip()][:6]
    if not words:
        return None
    unit = str(out.get("unit") or "").strip().lower()
    return {"property_terms": words, "unit": "none" if unit == "none" else canonical_unit(unit)}
