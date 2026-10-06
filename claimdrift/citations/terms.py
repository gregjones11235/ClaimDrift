"""Spellings of a superseded value (P1.3).

Citing papers write the same number in many ways: "5.8 days", "5·8 days" (Lancet style), "5.80 days", "5.8-day",
"5.8 d", "50.7 %"; a range "0 to 24" also as "0-24", "0–24", "0 and 24" (PMC7358146, "5.80 days", was missed by the
prototype; 21 Guan citers writing "0-24" / "between 0 and 24" were dropped by a regex that only knew "0 to 24"). The
pre-screen generates every spelling up front and uses the same rules for the Europe PMC query and the local regex.

  query_variants(term)  spellings to OR together in the Europe PMC full-text query (its index does the matching)
  term_regex(term)      one tolerant regex used locally to find the sentences in the downloaded full text; it matches
                        every spelling query_variants generates
  quantity_at / same_quantity  a match of a spelling proposed later (search_more) is read as the whole quantity it
                        belongs to and counts only when it equals an old value ("3.3" inside "3.3–5.47" yes, "R0 of
                        3.3" no; "75 out of 148" = 50.7%)
  quantity_sentences()  the local screen as a quantity match (value + unit + measured property, as in MeasEval /
                        Grobid-quantities): the value must match; a unit written next to it must be the target's unit;
                        a value written without a unit counts only if the measured property ("incubation period") is
                        in the same sentence (for a table row: the row, its column headers and the caption); a point
                        value that is the end of a range ("4.5–5.8") does not count. Spellings the orchestrator proposed
                        later (quantity["proposed"]) count only where the quantity equals an old value (see below) and
                        the property is there, even with a unit. The Europe PMC query searches the bare value
                        (value_spellings), so values whose unit is given elsewhere ("3.0 days (range, 0–24.0)") are found
  old_value_terms(...)  derive the superseded value(s) of a claim_diff -- the search terms of every citation target
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


RANGE_SEPS = [" to ", "-", "–", " and "]
RANGE_SEP_REGEX = r"\s*(?:-?\s*to|and|[-–—−])\s*"  # "0 to 24", "0- to 24", "0 and 24", "0-24", "0–24"
NUM_END = r"(?!\d|[.·∙⋅]\d)"  # "24" must not match inside "245" or "24.5"


def _norm(num: str) -> str:
    return num.replace(",", ".").replace("·", ".").replace("∙", ".").replace("⋅", ".")


def _split_range(term: str) -> tuple[str, str, str] | None:
    """'0 to 24' -> ('0', '24', ''); 'between 0 and 24.0 days' -> ('0', '24.0', 'days'); otherwise None."""
    m = re.fullmatch(rf"\s*(?:between\s+)?({NUM}){RANGE_SEP_REGEX}({NUM})(\s*-?\s*)(.*?)\s*", term, re.I)
    if not m:
        return None
    return _norm(m.group(1)), _norm(m.group(2)), m.group(4)


def _num_forms(num: str) -> list[str]:
    """Spellings of one number in a query, trailing zeros normalised like the local regex: 5.8 -> 5.8, 5·8, 5.80;
    3.30 -> 3.30, 3.3, 3·3; 24 -> 24, 24.0; 24.0 -> 24.0, 24."""
    base = num.rstrip("0").rstrip(".") if "." in num else num
    forms = [num, base] + ([base.replace(".", "·"), base + "0"] if "." in base else [base + ".0"])
    return _uniq(forms)


def _num_regex(num: str) -> str:
    if "." in num:
        ip, dp = num.split(".")
        dp = dp.rstrip("0")
        n = rf"{ip}{DECIMAL_SEPS}{dp}0*" if dp else rf"{ip}(?:{DECIMAL_SEPS}0+)?"
    else:
        n = rf"{num}(?:{DECIMAL_SEPS}0+)?"
    return rf"(?<![\d.·]){n}{NUM_END}"


def _unit_regex(unit: str) -> str:
    if not unit:
        return ""
    u = UNIT_REGEX.get(unit.lower(), r"\s*-?\s*".join(re.escape(w) for w in unit.split()))
    return (r"\s*" if unit == "%" else r"\s*-?\s*") + u


def _with_units(nums: list[str], unit: str) -> list[str]:
    forms = UNIT_FORMS.get(unit.lower(), [(" " + unit) if unit else ""])
    return [f"{n}{f}" if f.startswith(("-", " ", "%")) or not f else f"{n} {f}" for n in nums for f in forms]


def _uniq(xs: list[str]) -> list[str]:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _split(term: str) -> tuple[str | None, str, str]:
    """'5.8 days' -> ('5.8', ' ', 'days'); '50.7%' -> ('50.7', '', '%'); non-numeric terms -> (None, '', term)."""
    m = re.fullmatch(rf"\s*(\d{{1,3}}(?:,\d{{3}})+|{NUM})(\s*-?\s*)(.*?)\s*", term)
    if not m:
        return None, "", term.strip()
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", m.group(1)):  # thousands separator, not a decimal comma
        return None, "", term.strip()
    return m.group(1).replace(",", ".").replace("·", ".").replace("∙", ".").replace("⋅", "."), m.group(2), m.group(3)


def query_variants(term: str) -> list[str]:
    r = _split_range(term)
    if r:
        lo, hi, unit = r
        pairs = [f"{a}{sep}{b}" for sep in RANGE_SEPS for a in _num_forms(lo) for b in _num_forms(hi)]
        return _uniq([term.strip()] + _with_units(pairs, unit))
    num, _, unit = _split(term)
    if num is None:
        return [term.strip()]
    nums = _num_forms(num) if "." in num else [num]
    return _uniq([term.strip()] + _with_units(nums, unit))


def term_regex(term: str) -> re.Pattern:
    r = _split_range(term)
    if r:
        lo, hi, unit = r
        return re.compile(_num_regex(lo) + RANGE_SEP_REGEX + _num_regex(hi) + _unit_regex(unit), re.I)
    num, _, unit = _split(term)
    if num is None:
        words = [re.escape(w) for w in term.split()]
        return re.compile(r"\s*-?\s*".join(words), re.I)
    return re.compile(_num_regex(num) + _unit_regex(unit), re.I)


def numbers(term: str) -> list[str]:
    """The numbers of a value, normalised ('0 to 24.0 days' -> ['0', '24'], '5·80' -> ['5.8'], '29,500' -> ['29500'])."""
    out = []
    for m in re.finditer(rf"(?<![\d.·])(?:\d{{1,3}}(?:,\d{{3}})+(?![\d.·])|{NUM})", term):
        g = m.group(0)
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", g):
            out.append(g.replace(",", ""))
            continue
        n = _norm(g)
        out.append(n.rstrip("0").rstrip(".") if "." in n else n)
    return out


def _decimals(n: str) -> int:
    return len(n.split(".")[1]) if "." in n else 0


# A match is read as the whole quantity it belongs to (Grobid-quantities: single value, interval, list), and a fraction
# as the percentage it states; it counts for a spelling proposed later only when that quantity equals the old value.
_FRACTION = re.compile(r"(?<![\d.])(\d+)\s*(?:/|of|out of)\s*(\d+)(?![\d.])", re.I)
_RANGE_PREV = re.compile(rf"({NUM})\s*(?:-?\s*to|and|[-–—−])\s*$", re.I)
_RANGE_NEXT = re.compile(rf"\s*(?:-?\s*to|and|[-–—−])\s*({NUM})", re.I)


def target_quantity(term: str) -> tuple[float, ...]:
    """The old value as a quantity: '3.30 to 5.47' -> (3.3, 5.47), '50.7%' -> (50.7,), '29,500' -> (29500.0,)."""
    return tuple(float(n) for n in numbers(value_term(term)))


def quantity_at(text: str, start: int, end: int) -> tuple[tuple[float, ...], bool]:
    """The whole quantity a match at text[start:end] belongs to -> (values, computed). '3.3' inside 'between 3.3 and
    5.47' -> ((3.3, 5.47), False); '75 out of 148' -> ((50.67...,), True): a fraction, read as a percentage."""
    for f in _FRACTION.finditer(text):
        if f.start() < end and start < f.end() and float(f.group(2)):
            return (100 * float(f.group(1)) / float(f.group(2)),), True
    nums = numbers(text[start:end])
    if len(nums) >= 2:
        return tuple(float(n) for n in nums[:2]), False
    if not nums:
        return (), False
    v = float(nums[0])
    prev = _RANGE_PREV.search(text[max(0, start - 24):start])
    if prev:
        return (float(numbers(prev.group(1))[0]), v), False
    nxt = _RANGE_NEXT.match(text, end)
    if nxt:
        return (v, float(numbers(nxt.group(1))[0])), False
    return (v,), False


def same_quantity(found: tuple[float, ...], computed: bool, term: str) -> bool:
    """Equal after normalisation (an interval only as the whole interval). A computed value (a fraction) is compared at
    the precision the old value is stated with: 75/148 = 50.68 -> 50.7 equals 50.7%."""
    want = target_quantity(term)
    if not want or len(found) != len(want):
        return False
    if computed:
        decs = [_decimals(n) for n in numbers(value_term(term))]
        return all(round(f, d) == w for f, d, w in zip(found, decs, want))
    return all(f == w for f, w in zip(found, want))


# ---------------------------------------------------------------- quantity match (value + unit + measured property)
UNITS = [("%", r"%|per ?cent\b|percent\b"), ("days", r"days?\b|d\b"), ("hours", r"hours?\b|hrs?\b|h\b"),
         ("weeks", r"weeks?\b|wks?\b"), ("months", r"months?\b|mos?\b"), ("years", r"years?\b|yrs?\b|y\b")]
UNIT_NAMES = {u for u, _ in UNITS} | {"none"}
_UNIT_AFTER = [(u, re.compile(rf"\s*-?\s*(?:{rx})", re.I)) for u, rx in UNITS]
_RANGE_BEFORE = re.compile(rf"{NUM}\s*(?:-?\s*to|[-–—−])\s*$", re.I)
_RANGE_AFTER = re.compile(rf"\s*(?:-?\s*to|[-–—−])\s*{NUM}", re.I)


def in_range(text: str, start: int, end: int) -> bool:
    """A point value written as the end of a range ('95% CI 4.5-5.8', '5.8–7.0') is another quantity."""
    return bool(_RANGE_BEFORE.search(text[max(0, start - 20):start]) or _RANGE_AFTER.match(text, end))


def canonical_unit(unit: str | None) -> str | None:
    """'day' / 'd' / '-day' -> 'days', 'percent' -> '%'; unknown or empty -> None."""
    u = (unit or "").strip().lstrip("-").strip()
    return next((name for name, rx in _UNIT_AFTER if u and re.fullmatch(rx.pattern, u, re.I)), None)


def unit_after(text: str) -> str | None:
    """Canonical unit written right after a value ('-day incubation' -> 'days'), or None."""
    return next((name for name, rx in _UNIT_AFTER if rx.match(text)), None)


def value_term(term: str) -> str:
    """The value without its unit: '5.8 days' -> '5.8', '0 to 24.0 days' -> '0 to 24.0', '50.7%' -> '50.7'. Anything
    after the number that is not a known unit stays ('75 out of 148' is one expression)."""
    r = _split_range(term)
    if r and (not r[2] or canonical_unit(r[2])):
        return f"{r[0]} to {r[1]}"
    num, _, unit = _split(term)
    return num if num is not None and (not unit or canonical_unit(unit)) else term.strip()


def term_unit(term: str) -> str | None:
    """Canonical unit written in the term itself ('5.8 days' -> 'days', '0 to 24' -> None)."""
    r = _split_range(term)
    if r:
        return canonical_unit(r[2])
    num, _, unit = _split(term)
    return canonical_unit(unit) if num is not None else None


def value_spellings(term: str) -> list[str]:
    """Europe PMC query spellings of the bare value (its phrase search also finds every '<value> <unit>' form)."""
    return query_variants(value_term(term))


def value_regex(term: str) -> re.Pattern:
    return term_regex(value_term(term)) if numbers(term) else term_regex(term)


def _word_pattern(word: str) -> str:
    """'r0' also matches 'R 0' (a subscript R<sub>0</sub> becomes 'R 0' when the XML tags are stripped)."""
    return r"\s?".join(re.escape(part) for part in re.findall(r"[^\W\d_]+|\d+|\S", word))


def property_regex(quantity: dict | None) -> re.Pattern | None:
    words = [w.strip().lower() for w in (quantity or {}).get("property_terms") or [] if str(w).strip()]
    if not words:
        return None
    return re.compile(r"\b(?:" + "|".join(r"[\s-]+".join(_word_pattern(p) for p in w.split()) for w in words) + r")\b", re.I)


def quantity_sentences(sents: list[str], terms: list[str], quantity: dict | None,
                       contexts: list[str] | None = None) -> list[int]:
    """Indexes of the sentences holding the old value as the target's quantity. quantity = {"property_terms": [...],
    "unit": "days" | ... | "none", "proposed": [spellings added by search_more]} (citations.quantity). contexts[i], if
    given, is where else the property may stand for sents[i] (a table row's column headers and caption). Without a
    quantity (model call failed) a value without a unit counts only for terms that have no unit themselves -- the rule
    before the quantity step."""
    prop = property_regex(quantity)
    q_unit = (quantity or {}).get("unit")
    proposed = {p.strip().lower() for p in (quantity or {}).get("proposed") or []}
    targets = [t for t in terms if t.strip().lower() not in proposed and numbers(t)]  # the old values themselves
    rules = []
    for t in terms:
        own = term_unit(t)
        want = own or (q_unit if q_unit in UNIT_NAMES else None)  # "none": a count -- any unit next to it is another quantity
        strict = t.strip().lower() in proposed
        point = _split_range(value_term(t)) is None and not strict  # a point value, not a range
        rules.append((value_regex(t), bool(numbers(t)), want, own is not None, point, strict))
    keep = []
    for i, s in enumerate(sents):
        where = s + " " + (contexts[i] if contexts else "")
        for rx, numeric, want, has_unit, point, strict in rules:
            for m in rx.finditer(s):
                if not numeric:  # a spelling proposed later must be readable as a quantity to count
                    ok = not strict
                elif point and in_range(s, m.start(), m.end()):
                    ok = False
                else:
                    u = unit_after(s[m.end():m.end() + 16])
                    if u and want is not None and u != want:
                        ok = False
                    elif strict:  # a spelling proposed later: the quantity at the match must be an old value
                        found, computed = quantity_at(s, m.start(), m.end())
                        ok = any(same_quantity(found, computed, t) for t in targets) and (prop is None or bool(prop.search(where)))
                    elif u:
                        ok = True
                    elif prop is not None:
                        ok = bool(prop.search(where))
                    else:
                        ok = not has_unit
                if ok:
                    keep.append(i)
                    break
            if keep and keep[-1] == i:
                break
    return keep


def any_regex(terms: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{term_regex(t).pattern})" for t in terms), re.I)


# ---------------------------------------------------------------- automatic derivation from a claim_diff
# Digits that are not quantities (shared with the author self-check, selfcheck.claim_values): names such as COVID-19,
# years, "95% CI", citation and figure numbers, p values.
NOISE_PATTERNS = [r"\b[A-Za-z]+(?:-[A-Za-z]+)*-\d+[A-Za-z]*\b",   # COVID-19, SARS-CoV-2, IL-6, H5N1-like forms with a hyphen
                  r"\b[A-Za-z]+\d+[A-Za-z]*\d*\b",                 # CD4, H1N1, IL6, nCoV2019
                  r"\b\d{4}-nCoV\b", r"\b(?:19|20)\d{2}\b",         # 2019-nCoV, years
                  r"\b9[05](?:\.\d+)?\s*%?\s*(?:CI|confidence|credible|UI)\b",  # 95% CI / 90% credible interval
                  r"\[[\d,\s;–-]+\]", r"\b(?:Fig(?:ure)?s?|Tables?|Refs?|Supplementary|eTable|Appendix)\.?\s*S?\d+[A-Za-z]?\b",
                  r"\b[Pp]\s*[<=>≤≥]\s*0?[.·]\d+\b"]
_NOISE_RE = re.compile("|".join(NOISE_PATTERNS))
# an interval estimate around a value is not the value: "(95% CI: 2.73-3.96)", "(4.6 - 7.9, 95% CI)", "95% CI = 1.31–7.68"
_CI_GROUP = re.compile(r"[(\[][^()\[\]]*\b(?:CI|CrI|UI|confidence|credible)\b[^()\[\]]*[)\]]", re.I)
_CI_TAIL = re.compile(rf"\b9[05]\s*%\s*(?:CI|CrI|UI|confidence interval|credible interval)\s*[:=,]?\s*{NUM}\s*(?:-|–|—|to)\s*{NUM}", re.I)
_UNIT_TXT = r"(\s?%|\s?-?\s?(?:days?|weeks?|months?|years?)\b)?"
VALUE_WITH_UNIT = re.compile(rf"(?<![\w.·])({NUM}){_UNIT_TXT}", re.I)
RANGE_WITH_UNIT = re.compile(rf"(?<![\w.·])({NUM}){RANGE_SEP_REGEX}({NUM})(?![\d.·]){_UNIT_TXT}", re.I)


_CI_LABEL = re.compile(r"[,;]?\s*\b9[05]\s*%?\s*(?:CI|CrI|UI|confidence|credible)\b|[,;]?\s*\b(?:CI|CrI|UI)\b", re.I)


def _drop_interval(m: re.Match) -> str:
    """A bracket holding an interval estimate: drop it whole when it is only the interval ("(95%CI: 2.73-3.96)",
    "(4.6 - 7.9, 95% CI)"); keep what precedes the label when the value itself is inside ("(OR 3.1717, 95% CI 1.3-7.7)")."""
    inner = m.group(0)[1:-1]
    lab = _CI_LABEL.search(inner)
    before = inner[:lab.start()] if lab else ""
    if not lab or not before.strip() or re.fullmatch(rf"\s*{NUM}{RANGE_SEP_REGEX}{NUM}\s*[,;]?\s*", before):
        return " "
    return " " + before + " "


def _clean(text: str) -> str:
    t = _CI_GROUP.sub(_drop_interval, text or "")
    t = re.sub(r"\s+", " ", t)
    t = _CI_TAIL.sub(" ", t)
    return re.sub(r"\s+", " ", _NOISE_RE.sub(" ", t))


def _unit(u: str | None) -> str:
    u = (u or "").strip().lstrip("-").strip()
    return "%" if u.startswith("%") else (canonical_unit(u) or "") if u else ""


def _specific(nums: list[str], unit: str) -> bool:
    """Bare small integers are not searchable values (a full-text query for "34" matches nearly every paper)."""
    return bool(unit) or any("." in n for n in nums) or any(n.isdigit() and len(n) >= 4 for n in nums)


def stated_values(text: str | None) -> list[tuple[str, list[str]]]:
    """(term, normalised numbers) for every quantity a claim text states, ranges kept whole: "ranges from 3.30 (95%CI:
    2.73-3.96) to 5.47 (95%CI: 4.16-7.10)" -> [("3.30 to 5.47", ["3.3", "5.47"])]; interval estimates, years, names such
    as COVID-19 and p values are removed first."""
    t = _clean(text)
    out = []
    for m in RANGE_WITH_UNIT.finditer(t):
        unit = _unit(m.group(3))
        nums = numbers(f"{m.group(1)} {m.group(2)}")
        if _specific([m.group(1).replace(",", "."), m.group(2).replace(",", ".")], unit):
            out.append((f"{m.group(1)} to {m.group(2)}" + (unit if unit == "%" else f" {unit}" if unit else ""), nums))
    for m in VALUE_WITH_UNIT.finditer(RANGE_WITH_UNIT.sub(" ", t)):
        unit = _unit(m.group(2))
        num = m.group(1).replace("·", ".")
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", num):
            nums = [num.replace(",", "")]
        else:
            nums = numbers(num)
        if _specific([num.replace(",", "")], unit):
            out.append((num + (unit if unit == "%" else f" {unit}" if unit else ""), nums))
    return out


def old_value_terms(preprint_text: str | None, published_text: str | None, max_terms: int = 2) -> list[str]:
    """Values stated in the preprint claim that do not appear in the published claim, as search terms (ranges whole, units
    kept). A value is unchanged when all its numbers are stated in the published claim."""
    if not preprint_text:
        return []
    pub = {n for _, ns in stated_values(published_text) for n in ns} | set(numbers(_clean(published_text or "")))
    out = []
    for term, nums in stated_values(preprint_text):
        if all(n in pub for n in nums) or term in out:
            continue
        out.append(term)
        if len(out) >= max_terms:
            break
    return out
