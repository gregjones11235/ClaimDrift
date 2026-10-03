"""Abstract-level severity (§3.3, P0.3): Brierley et al. three classes (no_change | minor | major) from the two abstracts
alone, with the FIXED 12 human-labelled exemplars in the prompt (abstract_fewshot.py `few` condition; 72.8% -> 78.6%).

Stored and evaluated separately from the full-text tier: the two answer different questions against different gold
standards (Brierley 185 pairs vs gold.json), so they are never merged into one number. The exemplars are prompt guidance
from human labels, not self-calibration.
"""
from __future__ import annotations

import json
from functools import lru_cache

from . import config
from .llm import extract_json

CLASSES = ["no_change", "minor", "major"]

# Copied verbatim from data/cases/experiments/abstract_fewshot.py (DEFINITIONS and system_prompt("few", ...)).
DEFINITIONS = """You compare the abstract of a preprint with the abstract of its published journal version.
Follow the annotation scheme of Brierley et al. (PLOS Biology 2022):
- Only MEANINGFUL changes count. Formatting, stylistic edits, renamed terms and text rearrangements with no impact on the
  content are NOT changes.
- Each meaningful change gets a degree:
    1 = minor change (a statistic, detail or wording changed, including a conclusion that was slightly strengthened or softened)
    2 = major conclusions change (a conclusion or key result was added, removed or substantially altered)
    3 = the published abstract contradicts a conclusion of the preprint abstract
- The pair's class is decided by its highest degree:
    no_change = no meaningful change at all
    minor     = only degree-1 changes
    major     = at least one change of degree 2 or 3
Return ONLY JSON: {"changes": [{"section": "context|results|conclusions", "what": "<short>", "degree": 1|2|3}], "class": "no_change|minor|major"}
"""


def render(pre: str, pub: str) -> str:
    return f"PREPRINT ABSTRACT:\n{pre}\n\nPUBLISHED ABSTRACT:\n{pub}\n"


@lru_cache(maxsize=1)
def exemplar_file() -> dict:
    return json.loads((config.PROMPTS_DIR / "abstract_exemplars.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def system_prompt() -> str:
    parts = [DEFINITIONS, "\nBelow are pairs labelled by the human annotators. Use them to calibrate where the boundaries between the classes lie.\n"]
    for i, e in enumerate(exemplar_file()["exemplars"], 1):
        notes = "; ".join(e["annotator_notes"]) or "no meaningful change annotated"
        parts.append(f"### Labelled example {i}\n{render(e['pre'], e['pub'])}\nHUMAN ANNOTATION: {notes}\nHUMAN CLASS: {e['class']}\n")
    return "\n".join(parts)


def classify(backend, pre_abstract: str, pub_abstract: str) -> dict:
    """-> {"class": no_change|minor|major|None, "changes": [...], "exemplar_set": "...", "model": ...}"""
    r = backend.chat([{"role": "system", "content": system_prompt()},
                      {"role": "user", "content": render(pre_abstract, pub_abstract) + "\nClassify this pair. JSON only."}])
    out = extract_json(r["content"]) or {}
    ef = exemplar_file()
    return {"class": out.get("class") if out.get("class") in CLASSES else None,
            "changes": [c for c in out.get("changes") or [] if isinstance(c, dict)],
            "scale": "brierley_2022", "exemplar_set": f"brierley_k{ef['k']}_s{ef['seed']}", "model": backend.model}
