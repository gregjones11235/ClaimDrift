"""ClaimDrift command line (run inside WSL: `uv run python -m claimdrift <command>` from the repo root).
Targets the cloud system by default; CLAIMDRIFT_LOCAL=1 for the local stack (claimdrift/config.py).

  setup-es                               create / update the indices from elastic/mappings (cloud or local mappings)
  analyze <paper_id>... [--route R]      layer ①: analyse case-bank / docstore pairs, write drift_events, queue citation runs
  analyze-doi <preprint_doi> [<pub_doi>] layer ① on demand: fetch v1 + published JATS, then analyse
  citation-worker [--once]               layer ②: process queued citation runs (asynchronous to layer ①)
  rerun-citations [--days 7]             layer ②': queue incremental runs for targets last checked more than N days ago
  review-queue                           list items waiting for a human decision
  review <event|citation> <id> approved|rejected [--reviewer NAME] [--note ...] [--corrected JSON]
  notify [--dry-run]                     (re)send pending notifications for superseded/indirect citations (test inbox)
  selfcheck-index                        rebuild the author self-check index from drift_events
  selfcheck-sentence "<sentence>" | selfcheck-refs <file>
  eval-drift [--cases a,b] [--route stuffed|claims] [--tag T] [--no-es]
  eval-abstract [--limit N] | eval-citations <case> [--tools mcp|local] [--fresh] | eval-selfcheck [--mode hybrid|elser|bm25]
  score-citations <case>...              score the worker's affected_citations against citation_gold_v2.json
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys

from . import config


def _print(x) -> None:
    print(json.dumps(x, ensure_ascii=False, indent=1, default=str))


def cmd_setup_es(a) -> None:
    import os
    from . import es
    if config.LOCAL:  # 8.15 docker node: no copy_to on semantic_text, local ELSER endpoint
        subprocess.run([sys.executable, str(config.ROOT / "elastic" / "scripts" / "create_indices_local.py"),
                        "--endpoint", config.ES_ENDPOINT, "--inference-id", config.ELSER_INFERENCE_ID], check=True)
    else:  # Elasticsearch Serverless: the cloud mappings (.elser-2-elastic, refresh_interval >= 5s)
        env = dict(os.environ, ELASTIC_ENDPOINT=config.ES_ENDPOINT, ELASTIC_API_KEY=config.ES_API_KEY or "")
        subprocess.run([sys.executable, str(config.ROOT / "elastic" / "scripts" / "create_indices.py"), "--apply",
                        "--skip-existing"], check=True, env=env)
    _print({"indices": sorted(i["index"] for i in es.request("GET", "_cat/indices?format=json") if not i["index"].startswith("."))})


def cmd_analyze(a) -> None:
    from . import pipeline
    from .mcp_client import McpClient
    from .documents import case_bank_ids
    ids = case_bank_ids() if a.paper_ids == ["all"] else a.paper_ids
    with McpClient() as client:
        for pid in ids:
            ev = pipeline.analyze_paper(pid, mcp_client=client, force_route=a.route, enqueue_citations=not a.no_citations)
            _print({k: ev.get(k) for k in ("event_id", "paper_id", "analysis_route", "fulltext_severity", "verification",
                                            "review_status", "review_reasons", "citation_analysis")}
                   | {"abstract_class": ev["abstract_severity"].get("class"), "n_diffs": len(ev["claim_diffs"])})


def cmd_analyze_doi(a) -> None:
    from . import pipeline
    ev = pipeline.analyze_dois(a.preprint_doi, a.published_doi, enqueue_citations=not a.no_citations)
    _print({k: ev.get(k) for k in ("event_id", "paper_id", "fulltext_severity", "verification", "review_status")})


def cmd_worker(a) -> None:
    from .citations import jobs
    _print({"runs_processed": jobs.work(once=a.once, tools_via=a.tools)})


def cmd_rerun(a) -> None:
    from .citations import jobs
    _print({"queued_incremental_runs": jobs.rerun_due(a.days)})


def cmd_review_queue(a) -> None:
    from . import es
    ev = es.hits(config.INDICES["drift_events"], {"term": {"review_status": "pending"}}, size=200,
                 source_fields=["event_id", "paper_id", "review_reasons", "fulltext_severity.tier", "abstract_severity.class"])
    ac = es.hits(config.INDICES["affected_citations"], {"term": {"review_status": "pending"}}, size=1000,
                 source_fields=["affected_citation_id", "cites", "role", "review_reasons", "notify_priority", "sentence_verified"])
    _print({"events": ev, "citations": ac})


def cmd_review(a) -> None:
    from . import es, review, selfcheck
    index = config.INDICES["drift_events" if a.kind == "event" else "affected_citations"]
    doc = es.source(index, a.id)
    if doc is None:
        raise SystemExit(f"{a.kind} {a.id} not found")
    corrected = json.loads(a.corrected) if a.corrected else None
    review.decide(doc, a.decision, a.reviewer, a.note, corrected, kind=a.kind)
    es.put(index, a.id, doc)
    if a.kind == "event":
        selfcheck.index_event(doc)  # rejected events leave the self-check index
    _print({k: doc.get(k) for k in ("review_status", "reviewer", "reviewed_at", "review_note")})


def cmd_notify(a) -> None:
    from . import notifier
    _print(notifier.notify_pending(send=not a.dry_run))


def cmd_selfcheck_index(a) -> None:
    from . import selfcheck
    _print({"indexed_changes": selfcheck.rebuild_index()})


def cmd_selfcheck_sentence(a) -> None:
    from . import selfcheck
    _print(selfcheck.check_sentence(a.sentence, mode=a.mode))


def cmd_selfcheck_refs(a) -> None:
    from . import selfcheck
    _print(selfcheck.check_references(open(a.file, encoding="utf-8").read()))


def cmd_eval_drift(a) -> None:
    from . import evaluate
    from .documents import case_bank_ids
    cases = case_bank_ids() if a.cases == "all" else a.cases.split(",")
    _print(evaluate.run_drift(cases, a.route, a.tag or (a.route or "routed"), write_es=not a.no_es))


def cmd_eval_abstract(a) -> None:
    from . import evaluate
    _print(evaluate.run_abstract(a.limit))


def cmd_eval_citations(a) -> None:
    from . import evaluate
    _print(evaluate.run_citations(a.case, a.tools, a.fresh))


def cmd_score_citations(a) -> None:
    from . import evaluate
    _print({c: evaluate.score_citations_from_es(c) for c in a.cases})


def cmd_eval_selfcheck(a) -> None:
    from . import evaluate
    _print(evaluate.run_selfcheck(a.mode))


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="claimdrift")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("setup-es").set_defaults(fn=cmd_setup_es)
    p = sp.add_parser("analyze")
    p.add_argument("paper_ids", nargs="+", help="case-bank slugs / docstore ids, or 'all'")
    p.add_argument("--route", choices=["stuffed", "claims"], help="force a route (default: config.DEFAULT_ROUTE = claims)")
    p.add_argument("--no-citations", action="store_true")
    p.set_defaults(fn=cmd_analyze)
    p = sp.add_parser("analyze-doi")
    p.add_argument("preprint_doi")
    p.add_argument("published_doi", nargs="?")
    p.add_argument("--no-citations", action="store_true")
    p.set_defaults(fn=cmd_analyze_doi)
    p = sp.add_parser("citation-worker")
    p.add_argument("--once", action="store_true")
    p.add_argument("--tools", choices=["mcp", "local"], default="mcp")
    p.set_defaults(fn=cmd_worker)
    p = sp.add_parser("rerun-citations")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(fn=cmd_rerun)
    sp.add_parser("review-queue").set_defaults(fn=cmd_review_queue)
    p = sp.add_parser("review")
    p.add_argument("kind", choices=["event", "citation"])
    p.add_argument("id")
    p.add_argument("decision", choices=["approved", "rejected"])
    p.add_argument("--reviewer", default=None)
    p.add_argument("--note", default="")
    p.add_argument("--corrected", help='JSON with the human classification, e.g. {"cites": "indirect"} or {"fulltext_tier": "medium", "claim_diffs": [{"idx": 0, "root_cause": "reporting_choice"}]}')
    p.set_defaults(fn=cmd_review)
    p = sp.add_parser("notify")
    p.add_argument("--dry-run", action="store_true", help="store drafts only, do not send")
    p.set_defaults(fn=cmd_notify)
    sp.add_parser("selfcheck-index").set_defaults(fn=cmd_selfcheck_index)
    p = sp.add_parser("selfcheck-sentence")
    p.add_argument("sentence")
    p.add_argument("--mode", default="hybrid", choices=["hybrid", "elser", "bm25"])
    p.set_defaults(fn=cmd_selfcheck_sentence)
    p = sp.add_parser("selfcheck-refs")
    p.add_argument("file")
    p.set_defaults(fn=cmd_selfcheck_refs)
    p = sp.add_parser("eval-drift")
    p.add_argument("--cases", default="all")
    p.add_argument("--route", choices=["stuffed", "claims"])
    p.add_argument("--tag", default="")
    p.add_argument("--no-es", action="store_true", help="do not write drift_events")
    p.set_defaults(fn=cmd_eval_drift)
    p = sp.add_parser("eval-abstract")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(fn=cmd_eval_abstract)
    p = sp.add_parser("eval-citations")
    p.add_argument("case")
    p.add_argument("--tools", choices=["mcp", "local"], default="mcp")
    p.add_argument("--fresh", action="store_true", help="discard a saved checkpoint and start over")
    p.set_defaults(fn=cmd_eval_citations)
    p = sp.add_parser("score-citations", help="score the worker's affected_citations against citation_gold_v2.json")
    p.add_argument("cases", nargs="+")
    p.set_defaults(fn=cmd_score_citations)
    p = sp.add_parser("eval-selfcheck")
    p.add_argument("--mode", default="hybrid", choices=["hybrid", "elser", "bm25"])
    p.set_defaults(fn=cmd_eval_selfcheck)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
