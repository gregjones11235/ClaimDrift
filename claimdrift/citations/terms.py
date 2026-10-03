"""Spellings of a superseded value (P1.3).

Citing papers write the same number in many ways: "5.8 days", "5·8 days" (Lancet style), "5.80 days", "5.8-day",
"5.8 d", "50.7 %". The string pre-screen must catch all of them (PMC7358146, "5.80 days", was missed by the prototype).

  query_variants(term)  spellings to OR together in the Europe PMC full-text query (its index does the matching)
  term_regex(term)      one tolerant regex used locally to find the sentences in the downloaded full text
  old_value_terms(...)  derive the superseded value(s) of a claim_diff when no hand-written target exists
"""
from __future__ import annotations

import re

NUM = r"\d+(?:[.,·∙⋅]\d+)?"
DECIMAL_SEPS = "[.·∙⋅]"
UNIT_FORMS = {
    "days": ["days", "-day", " d"], "day": ["days", "-day", " d"], "d": ["days", "-day", " d"],
    "weeks": ["weeks", "-week"], "months": ["months", "-month"], "years": ["years", "-year"],
    "%": ["%", " %"], "percent": ["%", " %", " percent"],
}
UNIT_REGEX = {
    "days": r"(?:days?|d)\b", "day": r"(?:days?|d)\b", "d": r"(?:days?|d)\b",
    "weeks": r"weeks?\b", "months": r"months?\b", "years": r"years?\b",
    "%": r"(?:%|per ?cent)", "percent": r"(?:%|per ?cent)",
}


def _split(term: str) -> tuple[str | None, str, str]:
    """'5.8 days' -> ('5.8', ' ', 'days'); '50.7%' -> ('50.7', '', '%'); non-numeric terms -> (None, '', term)."""
    m = re.fullmatch(rf"\s*(\d{{1,3}}(?:,\d{{3}})+|{NUM})(\s*-?\s*)(.*?)\s*", term)
    if not m:
        return None, "", term.strip()
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", m.group(1)):  # thousands separator, not a decimal comma
        return None, "", term.strip()
    return m.group(1).replace(",", ".").replace("·", ".").replace("∙", ".").replace("⋅", "."), m.group(2), m.group(3)


def query_variants(term: str) -> list[str]:
    num, _, unit = _split(term)
    if num is None:
        return [term.strip()]
    nums = [num]
    if "." in num:
        nums += [num.replace(".", "·"), num + "0"]
    unit_key = unit.lower()
    forms = UNIT_FORMS.get(unit_key, [(" " + unit) if unit else ""])
    out = []
    for n in nums:
        for f in forms:
            out.append(f"{n}{f}" if f.startswith(("-", " ", "%")) or not f else f"{n} {f}")
    seen, uniq = set(), []
    for v in [term.strip()] + out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def term_regex(term: str) -> re.Pattern:
    num, _, unit = _split(term)
    if num is None:
        words = [re.escape(w) for w in term.split()]
        return re.compile(r"\s*-?\s*".join(words), re.I)
    if "." in num:
        ip, dp = num.split(".")
        dp = dp.rstrip("0")
        n = rf"{ip}{DECIMAL_SEPS}{dp}0*" if dp else rf"{ip}(?:{DECIMAL_SEPS}0+)?"
    else:
        n = rf"{num}(?:{DECIMAL_SEPS}0+)?"
    u = UNIT_REGEX.get(unit.lower(), r"\s*-?\s*".join(re.escape(w) for w in unit.split())) if unit else ""
    sep = r"\s*-?\s*" if unit and unit != "%" else r"\s*"
    return re.compile(rf"(?<![\d.·]){n}(?!\d)" + (sep + u if u else ""), re.I)


def any_regex(terms: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{term_regex(t).pattern})" for t in terms), re.I)


# ---------------------------------------------------------------- automatic derivation from a claim_diff
VALUE_WITH_UNIT = re.compile(rf"(?<![\w.·])({NUM})(\s?%|\s?-?\s?(?:days?|weeks?|months?|years?)\b)?", re.I)


def old_value_terms(preprint_text: str | None, published_text: str | None, max_terms: int = 2) -> list[str]:
    """Values stated in the preprint claim that do not appear in the published claim. Bare small integers are skipped
    (a full-text query for "34" matches nearly every paper); decimals, percentages and values with a unit are kept."""
    if not preprint_text:
        return []
    pub_norm = re.sub(r"[·∙⋅]", ".", published_text or "")
    out = []
    for m in VALUE_WITH_UNIT.finditer(preprint_text):
        num, unit = m.group(1).replace("·", "."), (m.group(2) or "").strip()
        if not unit and "." not in num and not (num.isdigit() and len(num) >= 4):
            continue
        if re.search(rf"(?<![\d.]){re.escape(num)}(?!\d)", pub_norm):
            continue
        term = f"{num}{unit}" if unit.startswith("%") else (f"{num} {unit.lstrip('-').strip()}" if unit else num)
        if term not in out:
            out.append(term)
        if len(out) >= max_terms:
            break
    return out
