"""drift_analyzer: the arbitration node (§3.3, P0.2 / P0.9). Single call, no ReAct branch.

  route "claims"  : DEFAULT (config.DEFAULT_ROUTE, decision 2026-10-02) -> claim_extractor (flash) per section chunk,
                    then ONE pro call over the two claim lists (M0)
  route "stuffed" : both full texts in one pro call; used when forced, or with DEFAULT_ROUTE="auto" for pairs whose
                    estimated v1 + published tokens are <= STUFF_LIMIT_TOKENS

Both routes use prompt v4a and return the same JSON. Evidence quotes are checked afterwards (provenance.py); nothing here
trusts them.
"""
from __future__ import annotations

import concurrent.futures as cf
import time

from . import claim_extractor, config, llm
from .documents import Pair
from .prompts import drift_analyzer as P

DIFF_TYPES = {"claim_disappeared", "claim_added", "numerical_shift", "hedging_added", "hedging_removed", "claim_reversed", "outcome_switch"}


def _call(backend, user: str) -> tuple[dict | None, str]:
    msgs = [{"role": "system", "content": P.SYSTEM}, {"role": "user", "content": user}]
    r = backend.chat(msgs)
    out = llm.extract_json(r["content"])
    if out is None:  # one repair turn; the experiments never needed more
        msgs += [r["assistant_message"], {"role": "user", "content": "That was not a JSON object. Output ONLY the final JSON object now."}]
        r = backend.chat(msgs)
        out = llm.extract_json(r["content"])
    return out, r["content"]


def normalize(out: dict | None) -> dict | None:
    """Coerce types and attach the per-diff full-text tier. Unknown labels are kept but flagged, never silently fixed."""
    if out is None:
        return None
    diffs = []
    for d in out.get("claim_diffs") or []:
        if not isinstance(d, dict):
            continue
        try:
            mat = float(d.get("materiality"))
        except (TypeError, ValueError):
            mat = None
        d["materiality"] = mat
        d["severity_tier"] = P.tier_of(mat)
        d["evidence"] = [e for e in d.get("evidence") or [] if isinstance(e, dict) and e.get("quote")]
        problems = []
        if d.get("root_cause") not in P.ROOT_CAUSE_SET:
            problems.append(f"root_cause {d.get('root_cause')!r} not in the closed set")
        if d.get("diff_type") not in DIFF_TYPES:
            problems.append(f"diff_type {d.get('diff_type')!r} not in the closed set")
        if problems:
            d["label_problems"] = problems
        diffs.append(d)
    out["claim_diffs"] = diffs
    try:
        out["materiality_score"] = float(out.get("materiality_score"))
    except (TypeError, ValueError):
        out["materiality_score"] = max((d["materiality"] for d in diffs if d["materiality"] is not None), default=None)
    tiers = [d["severity_tier"] for d in diffs if d["severity_tier"]]
    out["fulltext_tier"] = max(tiers, key=P.TIERS.index) if tiers else ("minor" if not diffs else None)
    return out


def analyze(pair: Pair, backend=None, force_route: str | None = None, extractor_factory=llm.flash,
            claims: dict[str, list[dict]] | None = None, extract_usage: dict | None = None) -> dict:
    """-> {"output", "route", "claims", "usage", "raw_tail", "secs"}. force_route: None | "stuffed" | "claims".
    `claims` = {pre_id: [...], "published": [...]} already extracted (the Playground runs the two extractors itself to show
    them as separate nodes); given claims imply the claims route."""
    backend = backend or llm.pro()
    pre, pub = pair.preprint, pair.published
    est = pair.est_tokens()
    route = "claims" if claims is not None else force_route or (config.DEFAULT_ROUTE if config.DEFAULT_ROUTE != "auto"
                            else ("stuffed" if est <= config.STUFF_LIMIT_TOKENS else "claims"))
    t0 = time.time()
    usage: dict = {}
    if route == "stuffed":
        claims = {}
        out, raw = _call(backend, P.stuffed_user(pair.pre_id, pre, pub))
    elif route == "claims":
        if claims is None:
            with cf.ThreadPoolExecutor(2) as ex:  # the two versions are independent
                f1 = ex.submit(claim_extractor.extract, pair.pre_id, pre, extractor_factory)
                f2 = ex.submit(claim_extractor.extract, "published", pub, extractor_factory)
                (c1, u1), (c2, u2) = f1.result(), f2.result()
            claims = {pair.pre_id: c1, "published": c2}
            extract_usage = {k: u1.get(k, 0) + u2.get(k, 0) for k in ("calls", "prompt_tokens", "output_tokens")} | {"model": u1["model"]}
        if extract_usage:
            usage["claim_extractor"] = extract_usage
        out, raw = _call(backend, P.claims_user(pair.pre_id, pre, pub, claim_extractor.compact(claims[pair.pre_id]),
                                                claim_extractor.compact(claims["published"])))
    else:
        raise ValueError(f"unknown route {route!r}")
    usage["drift_analyzer"] = backend.usage()
    return {"output": normalize(out), "route": {"route": route, "est_tokens": est, "limit": config.STUFF_LIMIT_TOKENS,
                                                "forced": force_route is not None, "prompt_version": P.PROMPT_VERSION},
            "claims": claims, "usage": usage, "raw_tail": (raw or "")[-4000:], "secs": round(time.time() - t0, 1)}
