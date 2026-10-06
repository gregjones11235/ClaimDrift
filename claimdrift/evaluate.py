"""Evaluation against the three gold standards, kept separate (§3.3: two severity scores are never merged).

  drift      gold.json (33 cases, 38 sub-drifts, v4a tiers): detection, root cause, full-text tier, quotes verified.
             Scored with the experiments' own scorer (run.score_case) so numbers are directly comparable with §8.
  abstract   Brierley et al. 185 pairs minus the 12 fixed exemplars = 173 test pairs: accuracy of the abstract class.
  citations  citation_gold_v2.json: recall / precision of "superseded" per target.
  selfcheck  real citing sentences from citation_gold_v2.json -> does path 2 find the right event and the right old/new value?

Results are written to data/cases/experiments/results/prod_*.json.
"""
from __future__ import annotations

import collections
import json
import sys
import time

from . import config

RESULTS = config.EXPERIMENTS_DIR / "results"


def _exp():
    if str(config.EXPERIMENTS_DIR) not in sys.path:
        sys.path.insert(0, str(config.EXPERIMENTS_DIR))


def score_drift_case(case: str, output: dict | None) -> list[dict]:
    _exp()
    import run as R  # experiments scorer
    from corpus import load_case
    from tools import ToolBox
    return R.score_case(case, output, ToolBox(load_case(str(config.CASES_DIR / case))), [])


def drift_summary(rows: list[dict]) -> dict:
    pos = [r for r in rows if not r["subdrift"].startswith("N0")]
    neg = [r for r in rows if r["subdrift"].startswith("N0")]
    return {"subdrifts": len(pos), "detected": sum(r["detected"] for r in pos), "root_cause_ok": sum(r["root_cause_ok"] for r in pos),
            "tier_exact": sum(r["tier_ok"] for r in pos), "quotes": sum(r["n_evidence"] for r in pos),
            "quotes_grounded": sum(r["grounded"] for r in pos), "negatives_ok": f"{sum(r['root_cause_ok'] for r in neg)}/{len(neg)}"}


def run_drift(cases: list[str], force_route: str | None, tag: str, write_es: bool) -> dict:
    """Run the production pipeline on case-bank pairs and score every case. Resumable."""
    from . import pipeline
    from .documents import load_pair
    from .mcp_client import McpClient
    path = RESULTS / f"prod_drift_{tag}.json"
    res = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"force_route": force_route, "cases": {}}
    with McpClient() as client:
        for case in cases:
            if res["cases"].get(case, {}).get("output") is not None:
                continue
            t0 = time.time()
            try:
                ev = pipeline.analyze_pair(load_pair(case), mcp_client=client, force_route=force_route, write=write_es,
                                           enqueue_citations=False)
            except Exception as e:  # noqa: BLE001
                print(f"[{case}] ERROR {type(e).__name__}: {str(e)[:300]}", flush=True)
                continue
            out = {"drift_summary": ev["drift_summary"], "claim_diffs": ev["claim_diffs"], "materiality_score": ev["materiality_score"]}
            rows = score_drift_case(case, out)
            res["cases"][case] = {"output": out, "scores": rows, "route": ev["analysis_route"], "abstract_severity": ev["abstract_severity"],
                                  "fulltext_severity": ev["fulltext_severity"], "verification": ev["verification"],
                                  "review_status": ev["review_status"], "review_reasons": ev["review_reasons"],
                                  "usage": ev.get("usage"), "wall_secs": round(time.time() - t0, 1)}
            path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[{case}] {ev['analysis_route']['route']} " + " ".join(
                f"{r['subdrift'][:12]}:{'RC✓' if r['root_cause_ok'] else 'RC✗'}/{r['tier_pred']}" for r in rows)
                + f" quotes {ev['verification']['quotes_verified']}/{ev['verification']['quotes_total']} abs={ev['abstract_severity'].get('class')}"
                + f" {round(time.time() - t0)}s", flush=True)
    rows = [r for c in res["cases"].values() for r in c["scores"]]
    res["summary"] = drift_summary(rows) | {"cases_done": len(res["cases"]), "cases_requested": len(cases)}
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res["summary"]


def run_abstract(limit: int = 0) -> dict:
    _exp()
    import abstract_fewshot as AF
    from . import abstract_severity as A
    from . import llm
    ef = A.exemplar_file()
    ex, test = AF.split(AF.load_pairs(), ef["k"], ef["seed"])
    assert [e["doi"] for e in ex] == [e["doi"] for e in ef["exemplars"]], "exemplar file out of sync with the split"
    test = test[:limit] if limit else test
    path = RESULTS / "prod_abstract_severity.json"
    done = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": {}}
    backend = llm.pro()
    for p in test:
        if p["doi"] in done["items"]:
            continue
        r = A.classify(backend, p["pre"], p["pub"])
        done["items"][p["doi"]] = {"gold": p["class"], "pred": r["class"]}
        path.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    items = [done["items"][p["doi"]] for p in test if p["doi"] in done["items"]]
    conf = collections.Counter((v["gold"], v["pred"] or "invalid") for v in items)
    acc = sum(c for (g, p), c in conf.items() if g == p) / len(items) if items else None
    summary = {"n": len(items), "accuracy": round(acc, 3) if acc is not None else None,
               "confusion": {f"{g}->{p}": c for (g, p), c in sorted(conf.items())}}
    done["summary"] = summary
    path.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def score_citations(case: str, works: list[dict]) -> dict:
    gold = json.loads((config.EXPERIMENTS_DIR / "citation_gold_v2.json").read_text(encoding="utf-8"))["targets"].get(case)
    if gold is None:
        return {"error": f"no citation gold for {case}"}
    g = set(gold.get("superseded") or {})
    pred = {w["work_id"] for w in works if w.get("cites") == "superseded"}
    tp = pred & g
    cls = {w: "superseded" for w in g} | {w: "flagged_as_previous" for w in gold.get("flagged_as_previous") or []} \
        | {w: "indirect" for w in gold.get("indirect") or []} | {w: "current" for w in gold.get("current") or []}
    judged = [w for w in works if w.get("work_id")]
    correct = sum(1 for w in judged if w.get("cites") == cls.get(w["work_id"], "not_relying"))
    return {"gold_superseded": len(g), "predicted_superseded": len(pred), "tp": len(tp),
            "recall": round(len(tp) / len(g), 3) if g else None, "precision": round(len(tp) / len(pred), 3) if pred else None,
            "accuracy_over_judged": round(correct / len(judged), 3) if judged else None, "n_judged": len(judged),
            "missed": sorted(g - pred), "false_superseded": sorted(pred - g),
            "unverified_superseded": sum(1 for w in works if w.get("cites") == "superseded" and not w.get("sentence_verified"))}


def score_citations_from_es(case: str) -> dict:
    """Score what the asynchronous worker wrote to affected_citations for a case-bank paper."""
    from . import es
    ev = es.hits(config.INDICES["drift_events"], {"term": {"paper_id": case}}, size=1)
    if not ev:
        return {"error": f"no drift event for {case}"}
    works = es.hits(config.INDICES["affected_citations"], {"term": {"drift_event_id": ev[0]["event_id"]}}, size=10000)
    runs = es.hits(config.INDICES["citation_runs"], {"term": {"drift_event_id": ev[0]["event_id"]}}, size=50,
                   source_fields=["status", "kind", "n_candidates", "n_judged", "n_unjudged", "coverage_complete", "judged_by", "calls", "tokens", "counts"])
    return {"runs": runs} | score_citations(case, works)


_STRENGTH = ["superseded", "indirect", "flagged_as_previous", "unclear", "current", "not_relying"]


def _run_target(t: dict, ckpt, tools_via: str, client) -> dict:
    """One citation target, step by step, saving the run's state after every step (the same state the citation queue
    keeps in citation_runs), so an interrupted run resumes from its last completed step."""
    from .citations.orchestra import Orchestra
    from .citations.runner import make_access
    if ckpt.exists():
        state = json.loads(ckpt.read_text(encoding="utf-8"))
        print(f"resuming {t['target_id']} from phase {state['phase']} (turn {state.get('turns', 0)})", file=sys.stderr, flush=True)
        o = Orchestra(state["target"], access=make_access(state["target"], tools_via, client), state=state)
    else:
        o = Orchestra(t, access=make_access(t, tools_via, client))
    while o.phase != "done":
        o.step()
        tmp = ckpt.with_suffix(".tmp")
        tmp.write_text(json.dumps(o.to_state(), ensure_ascii=False, default=list), encoding="utf-8")
        tmp.replace(ckpt)
    return o.final


def _merge(case: str, results: list[dict]) -> dict:
    """One paper's targets -> one result: per citing paper the strongest verdict over its targets (superseded first)."""
    works: dict[str, dict] = {}
    for r in results:
        for w in r["citing_works"]:
            cur = works.get(w["work_id"])
            if cur is None or _STRENGTH.index(w["cites"]) < _STRENGTH.index(cur["cites"]):
                works[w["work_id"]] = w | {"target_id": r["target_id"]}
    cands = {c for r in results for c in [w["work_id"] for w in r["citing_works"]] + [u["work_id"] for u in r["unjudged"]]}
    unjudged = {u["work_id"]: u for r in results for u in r["unjudged"] if u["work_id"] not in works}
    add = lambda key: {k: sum((r.get(key) or {}).get(k, 0) for r in results) for k in {k for r in results for k in (r.get(key) or {})}}
    return {"case": case, "mode": "orchestra", "targets": [{k: r.get(k) for k in ("target_id", "terms", "n_candidates", "n_judged",
            "n_unjudged", "judged_by", "calls", "tokens", "orchestrate_s", "coverage_notes")} | {"quantity": (r.get("events") or [{}])[0].get("quantity"),
            "prefetch_secs": (r.get("prefetch") or {}).get("secs")} for r in results],
            "n_candidates": len(cands), "n_judged": len(works), "n_unjudged": len(unjudged), "unjudged": list(unjudged.values()),
            "coverage_complete": all(r["coverage_complete"] for r in results), "judged_by": add("judged_by"), "calls": add("calls"),
            "tokens": add("tokens"), "orchestrate_s": round(sum(r.get("orchestrate_s") or 0 for r in results), 1),
            "prefetch_secs": round(sum((r.get("prefetch") or {}).get("secs") or 0 for r in results), 1),
            "citing_works": list(works.values()), "per_target": results}


def run_citations(case: str, tools_via: str = "mcp", fresh: bool = False) -> dict:
    """Citation analysis of a case-bank paper WITHOUT the queue (for evaluation); writes nothing to ES. The targets are
    built exactly as in production (citations.targets.build_targets on the paper's drift event: one target per changed
    claim with a traceable value, search terms derived from the claim text), each run step by step with a checkpoint
    (prod_citation__<case>__<diff>.state.json; fresh=True discards them); the verdicts are merged per citing paper."""
    from . import es
    from .citations.targets import build_targets
    ev = es.hits(config.INDICES["drift_events"], {"term": {"paper_id": case}}, size=1)
    if not ev:
        raise SystemExit(f"no drift event for {case}")
    targets = build_targets(ev[0], case)
    client = None
    if tools_via == "mcp":
        from .mcp_client import McpClient
        client = McpClient()
    results = []
    try:
        for t in targets:
            idx = t["claim_diff_idx"]
            t = t | {"target_id": f"eval::{case}::{idx}", "drift_event_id": f"eval::{case}"}
            ckpt = RESULTS / f"prod_citation__{case}__{idx}.state.json"
            if fresh and ckpt.exists():
                ckpt.unlink()
            results.append(_run_target(t, ckpt, tools_via, client))
    finally:
        if client is not None:
            client.close()
    res = _merge(case, results)
    res["score"] = score_citations(case, res["citing_works"])
    (RESULTS / f"prod_citation__{case}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    for t in targets:
        (RESULTS / f"prod_citation__{case}__{t['claim_diff_idx']}.state.json").unlink(missing_ok=True)
    return {"targets": [(x["target_id"], x["terms"]) for x in res["targets"]]} | {k: res[k] for k in ("n_candidates", "n_judged", "n_unjudged", "coverage_complete", "judged_by", "calls", "tokens", "orchestrate_s")} | res["score"]


def run_selfcheck(mode: str = "hybrid") -> dict:
    """Path 2 on the human-verified citing sentences of citation_gold_v2.json (selfcheck_eval.queries)."""
    _exp()
    import selfcheck_eval as SE
    from . import es, selfcheck
    events = {e["paper_id"]: e for e in es.hits(config.INDICES["drift_events"], {"term": {"record_source": "case_bank"}}, size=500)
              if e.get("paper_id")}
    rows = []
    for q in SE.queries():
        r = selfcheck.check_sentence(q["text"], mode=mode)
        ranked = [m["paper_id"] for m in r["matches"] + r["weak_matches"]]  # retrieval quality, before the relevance floor
        rank = ranked.index(q["case"]) + 1 if q["case"] in ranked else None
        vc = next((m["value_check"]["verdict"] for m in r["matches"] + r["weak_matches"] if m["paper_id"] == q["case"]), None)
        want = "uses_old_value" if q["kind"] == "superseded" else "uses_current_value"
        rows.append({k: q[k] for k in ("work_id", "case", "kind", "number")} | {"rank": rank, "value_verdict": vc, "value_ok": vc == want,
                                                                                 "above_floor": r["match"] != "no_match",
                                                                                 "event_in_library": q["case"] in events})
    n = len(rows)
    summary = {"n": n, "hit1": sum(r["rank"] == 1 for r in rows), "hit3": sum(bool(r["rank"]) for r in rows),
               "above_floor": sum(r["above_floor"] for r in rows),
               "old_new_correct": sum(r["value_ok"] for r in rows),
               "old_new_correct_with_number": f"{sum(r['value_ok'] for r in rows if r['number'] == 'with_number')}/{sum(r['number'] == 'with_number' for r in rows)}"}
    (RESULTS / f"prod_selfcheck_{mode}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
