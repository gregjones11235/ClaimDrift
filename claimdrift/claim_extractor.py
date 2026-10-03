"""claim_extractor (§3.3.1, P0.9).

Runs on every pair by default (config.DEFAULT_ROUTE = "claims", decision 2026-10-02); with DEFAULT_ROUTE="auto" only
pairs above STUFF_LIMIT_TOKENS reach it. Runs on
flash -- the "drift_analyzer only on pro" rule does not apply here. Each version is cut into section chunks of at most
24,000 characters, each chunk carries the version's full outline, chunks run in parallel. Extracts findings (numbers kept
verbatim) and method/definition statements. `support_sections` is extracted and stored for inspection but deliberately
NOT used to read sections (condition M in the experiment pushed 6 severities up one tier and missed 2 changes).
Prompt and chunking copied from data/cases/experiments/mapreduce.py (condition M0).
"""
from __future__ import annotations

import concurrent.futures as cf
import json

from . import config, llm
from .corpus import Doc

EXTRACT_SYS = """You extract the scientific claims of one part of a paper, for later comparison with another version of the same paper.
Extract:
- findings: every result, estimate, comparison or conclusion the paper states (keep numbers exactly);
- method_definition: every statement that defines a population, outcome, exposure, case definition, time window, model,
  estimator, inclusion/exclusion rule or data source that a finding depends on.
For each claim, `support_sections` lists the section titles FROM THE OUTLINE where the method, definition or data behind
this claim is described (where someone would look to understand WHY this number is what it is). Use exact outline titles.
Return ONLY JSON:
{"claims": [{"section": "<title of the section this sentence is in>", "text": "<VERBATIM sentence>",
  "kind": "finding|method_definition", "numbers": [{"metric": "...", "value": "...", "unit": "..."}],
  "support_sections": ["<outline title>", ...]}]}"""


def chunks(doc: Doc, limit: int = config.EXTRACT_CHUNK_CHARS) -> list[str]:
    parts = [("Abstract", doc.abstract)] + [(s.title, s.text) for s in doc.sections] + [(f"Table: {lbl}", t) for lbl, t in doc.tables]
    cur, size, out = [], 0, []
    for title, text in parts:
        block = f"## {title}\n{text}"
        if cur and size + len(block) > limit:
            out.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(block[:limit])
        size += len(block)
    if cur:
        out.append("\n\n".join(cur))
    return out


def extract(doc_id: str, doc: Doc, backend_factory=llm.flash, workers: int = config.EXTRACT_WORKERS) -> tuple[list[dict], dict]:
    """-> (claims with program-assigned ids, usage). One backend per chunk so calls run in parallel."""
    outline = doc.outline()

    def one(ch: str):
        b = backend_factory()
        r = b.chat([{"role": "system", "content": EXTRACT_SYS},
                    {"role": "user", "content": f"OUTLINE: {json.dumps(outline, ensure_ascii=False)}\n\nPART OF THE PAPER:\n{ch}"}])
        return (llm.extract_json(r["content"]) or {}).get("claims") or [], b.usage()

    claims, usage = [], {"calls": 0, "prompt_tokens": 0, "output_tokens": 0}
    with cf.ThreadPoolExecutor(workers) as ex:
        for cl, u in ex.map(one, chunks(doc)):
            claims += [c for c in cl if isinstance(c, dict) and c.get("text")]
            for k in usage:
                usage[k] += u.get(k, 0)
    for i, c in enumerate(claims):
        c["id"] = f"{doc_id[:3]}{i}"
        c["doc_id"] = doc_id
    usage["model"] = config.MODEL_FLASH
    return claims, usage


def compact(claims: list[dict]) -> str:
    return "\n".join(json.dumps({k: c.get(k) for k in ("id", "section", "kind", "text", "numbers", "support_sections")}, ensure_ascii=False)
                     for c in claims)
