"""citation analysis as orchestrator-workers (§3.5, P0.5) -- the one real agent of the system: what to read next depends
on intermediate results.

  quantity (flash)      what the old value measures (property + unit), one call before the pre-screen (quantity.py)
  prefetch (program)    candidates = citing papers whose open full text contains the old value as that quantity
                        (prefetch.py, terms.quantity_sentences), with flags
  orchestrator (pro)    overview / dispatch / search_more / follow_chain / finish; budget 14 calls
  workers (pro)         <= 8 per dispatch, <= 5 papers each, <= 3 dispatch rounds; each in a fresh context, tools
                        get_citation_sentences / search_in_work through the MCP tool service (P1.6); 3 tool calls per paper
  leftovers (program)   candidates the orchestrator did not send to a worker are dispatched to workers by the program,
                        8 groups of 5 per step, until WORKER_CAPACITY papers (120) have been sent in the run; candidates
                        beyond it stay unjudged and the run is reported truncated (very large targets are cut on purpose)
  verification (pro)    before the summary, every "superseded" verdict is checked again against its sentence and
                        reference (P1.2)

Verdict classes: superseded | current | flagged_as_previous | indirect | not_relying | unclear.
"superseded" requires: (1) the reference list matches the target, (2) the old value is attributed to the target,
(3) it is used as a finding or parameter (not flagged as an earlier version, no new value alongside).
Coverage is always reported (n_judged / n_unjudged / coverage_complete / unjudged) -- a capped run must never look
complete.

Every Europe PMC operation goes through a citations.access object (local, or the MCP tool service when the orchestra
runs on Agent Engine). A run can be executed in one go (run()) or one step at a time (step() + to_state() /
Orchestra(..., state=...)); both execute exactly the same sequence of calls.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import threading
import time

from .. import config, llm
from . import epmc
from .quantity import quantity_context
from .terms import any_regex
from .access import WORKER_TOOLS  # noqa: F401 -- re-exported (jobs, evaluate, selfcheck import it from here)

VERDICTS = {"superseded", "current", "flagged_as_previous", "indirect", "not_relying", "unclear"}
ROLES = {"model_input", "reported_as_fact", "background", "unknown"}

UNCLEAR_RULES = """Rules for "unclear" (use it whenever one holds and the sentences you read do not settle the case):
 F1 the old value is not in (or next to) a sentence that cites the target through the reference list -- e.g. it is
    attributed to ANOTHER paper (then it may be "indirect") or its source cannot be determined;
 F2 the value appears only in a table row or list without an explanatory sentence;
 F3 the sentence states BOTH the superseded and the current value;
 M1 you cannot tell whether the value is the target's finding;
 M2 you cannot tell whether the value is used as a model input or only reported/mentioned."""

WORKER_SYSTEM = """You judge how specific citing papers use a revised preprint claim.
Target: {author} et al. Superseded preprint (v1) claim: {old}. Current claim: {new}.
For EACH paper assigned to you, read where the target is cited (get_citation_sentences) and, if needed, how the value is used
(search_in_work). Budget {budget} tool calls in total. Then return ONLY JSON:
{{"verdicts": [{{"work_id": "...", "cites": "superseded|current|flagged_as_previous|indirect|not_relying|unclear",
  "role": "model_input|reported_as_fact|background|unknown", "sentence": "<VERBATIM sentence>",
  "relayed_by": "<work_id or reference text of an intermediary paper if the old value is attributed to ANOTHER paper, else empty>",
  "unclear_rule": "<F1|F2|F3|M1|M2 or empty>", "reason": "<one line>"}}]}}
"superseded" = the paper itself states the old value as the target's finding. "indirect" = the old value is attributed to
another paper that relayed it. "flagged_as_previous" = quoted as an earlier/revised estimate. "not_relying" = the number is a
coincidence, belongs to another study or quantity, or the paper does not use the target's claim.
The program pre-screened every paper and attached deterministic flags. Flags tell you WHAT TO CHECK; they are not verdicts:
 F1 (old value not next to a sentence citing the target): check whether the value is credited to ANOTHER paper ("indirect")
    or to the target in some other way (a table row naming the target, a description that unambiguously identifies the
    target study). If it is the target's value, the paper can still be "superseded".
 F2 (value only in a table row or list): a table row that names or cites the target (e.g. a parameter table row
    "Incubation period | 5.8 days | Backer et al.") IS a use of the target's value -- usually role model_input.
    Use "unclear" only if the source of the row cannot be identified.
 F3 (old and current value in the same sentence): check whether the old value is presented as an earlier/revised estimate
    ("flagged_as_previous") or still used.
Use "unclear" only when, after reading, the case is still not settled:
""" + UNCLEAR_RULES

ORCH_SYSTEM = """You are the lead citation analyst (orchestrator) of a claim-drift monitoring system.
Goal: find ALL citing papers that still rely on the superseded preprint claim, including indirect propagation through papers
that relayed the old value, and stop when further search is unlikely to find more.
Superseded claim: {old}. Current claim: {new}.

You do not read papers yourself. Your tools:
- overview(page, only_unjudged): the candidates found so far (program pre-screening), {ps} per page, and which are already
  judged. Read every page before planning: candidates beyond the first page are NOT shown on it.
- dispatch(groups): send groups of candidate work_ids to parallel workers; each worker judges its group in a fresh context.
  At most {mw} groups per call, at most {mp} papers per group, at most {mr} dispatch calls in total.
- search_more(terms): pre-screen citing papers again with spellings of the old value the pre-screen did not cover. It
  already searched decimal separators ('5·8'), trailing zeros ('5.80'), unit forms ('5.8-day', '5.8 d') and range forms
  ('0-24', '0–24', '0 and 24'). Each match is read as a quantity and counts only when it equals the old value (a
  range only as the whole range; a fraction such as '75 out of 148' when it equals the percentage) and the measured
  property is in the same sentence or table row.
- follow_chain(intermediary): for an intermediary paper that relayed the old value (a PMC id, or 'Author Year' + title words),
  pre-screen ITS citing papers for the old value (second hop). At most {mh} hops.
- finish(summary): end the investigation. Candidates you did not dispatch are then sent to workers by the program while
  the run's worker capacity ({cap} papers) lasts, in candidate order; beyond it they stay unjudged. So dispatch the
  candidates you consider most likely to rely on the old value first.
Plan from the overview; prioritise unjudged candidates; follow chains only when workers report "indirect"."""

VERIFY_SYSTEM = """You double-check verdicts claiming that a citing paper relies on a SUPERSEDED preprint claim.
Target: {author} et al. Superseded preprint (v1) claim: {old}. Current claim: {new}.
A "superseded" verdict stands only if ALL three conditions hold on the evidence shown:
 (1) the paper's reference list contains the target (reference_matched is true, or the target is identifiable by DOI or by
     author plus title);
 (2) the old value is attributed to the target: the in-text citation on that sentence, on the adjacent sentence, or on the
     table row/caption that holds the value points to it, or the text unambiguously identifies the target study (e.g. by
     author, sample size and setting);
 (3) the value is used as the target's finding or as a parameter: not described as an earlier/revised estimate, and the
     current value is not given alongside.
Otherwise give the correct class: "indirect" (the value is credited to another paper that relayed it; set relayed_by),
"flagged_as_previous", "current", "not_relying" (coincidental number, other study or quantity), or "unclear" (the evidence
does not settle it; set unclear_rule F1|F2|F3|M1|M2).
`sentence` must be copied verbatim from the evidence shown (prefer the worker's sentence if it is verbatim).
Return ONLY JSON: {{"checks": [{{"work_id": "...", "verdict": "confirm|indirect|flagged_as_previous|current|not_relying|unclear",
  "condition_1": true|false, "condition_2": true|false, "condition_3": true|false, "sentence": "<VERBATIM>",
  "relayed_by": "", "unclear_rule": "", "reason": "<one line>"}}]}}"""

LOCAL_WORKER_SCHEMAS = [
    {"type": "function", "function": {"name": "get_citation_sentences", "description": "Sentences of one citing paper in which the target is cited (matched through its reference list), each with its neighbouring sentences.",
        "parameters": {"type": "object", "properties": {"work_id": {"type": "string"}}, "required": ["work_id"]}}},
    {"type": "function", "function": {"name": "search_in_work", "description": "Sentences of one citing paper containing ALL comma-separated terms (e.g. to see whether a value is a model input).",
        "parameters": {"type": "object", "properties": {"work_id": {"type": "string"}, "terms": {"type": "string"}}, "required": ["work_id", "terms"]}}},
]


def run_worker(target: dict, papers: list[str], worker_tools, backend, call=None) -> list[dict]:
    """One worker: judge the listed citing papers (lines "- <work_id> (flags: ...; pre-screen sentence: ...)") with the
    worker tools (get_citation_sentences / search_in_work; MCP-bound or local). Shared by the citation orchestra and the
    author self-check of an already published paper, so both judge with the same prompt and the same tools."""
    budget = 3 * len(papers)
    system = WORKER_SYSTEM.format(author=target.get("first_author") or "the", old=target["drift"]["preprint_v1_claim"],
                                  new=target["drift"]["current_claim"], budget=budget)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": "Your papers:\n" + "\n".join(papers)}]
    final = llm.tool_loop(backend, msgs, worker_tools.schemas, call or worker_tools.call, budget, budget + 6,
                          exhausted_msg="Tool budget exhausted. Output ONLY the JSON now.")
    return [v for v in (llm.extract_json(final) or {}).get("verdicts") or [] if isinstance(v, dict)]


class LocalWorkerTools:
    """Same contract as mcp_client.BoundTools, backed by in-process functions (tests / --tools local)."""

    def __init__(self, tools: epmc.CitationTools):
        self.tools, self.schemas, self.log = tools, LOCAL_WORKER_SCHEMAS, []

    def invoke(self, name: str, args: dict):
        if name not in WORKER_TOOLS:
            return {"error": f"tool {name} not available"}
        return getattr(self.tools, name)(**args)

    def call(self, name: str, args: dict) -> str:
        try:
            out = json.dumps(self.invoke(name, args or {}), ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            out = json.dumps({"error": f"{type(e).__name__}: {e}"})
        self.log.append({"tool": name, "args": args, "result_chars": len(out), "result_digest": out[:200], "via": "local"})
        return out


class Usage:
    def __init__(self, calls: dict | None = None, tokens: dict | None = None):
        self.lock = threading.Lock()
        self.calls = {"orchestrator": 0, "workers": 0, "verifier": 0, "batch": 0} | (calls or {})
        self.tokens = {k: 0 for k in self.calls} | (tokens or {})

    def add(self, who: str, backend) -> None:
        with self.lock:
            self.calls[who] += backend.calls
            self.tokens[who] += backend.prompt_tokens + backend.output_tokens


ORCH_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "overview", "description": f"Candidates so far ({config.OVERVIEW_PAGE_SIZE} per page) and their judgement status.",
        "parameters": {"type": "object", "properties": {"page": {"type": "integer"}, "only_unjudged": {"type": "boolean"}}}}},
    {"type": "function", "function": {"name": "dispatch", "description": f"Send up to {config.ORCH_MAX_WORKERS} groups (each up to {config.ORCH_MAX_PAPERS} work_ids) to parallel workers.",
        "parameters": {"type": "object", "properties": {"groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}, "required": ["groups"]}}},
    {"type": "function", "function": {"name": "search_more", "description": "Pre-screen citing papers again with spellings or equivalent forms the pre-screen did not cover (comma-separated).",
        "parameters": {"type": "object", "properties": {"terms": {"type": "string"}}, "required": ["terms"]}}},
    {"type": "function", "function": {"name": "follow_chain", "description": "Pre-screen the citing papers of an intermediary paper that relayed the old value (second hop).",
        "parameters": {"type": "object", "properties": {"intermediary": {"type": "string"}}, "required": ["intermediary"]}}},
    {"type": "function", "function": {"name": "finish", "description": "End the investigation.", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]

# Phases of a run. Each step() does one unit of work, so a run can be split across calls that each stay well below
# Agent Engine's request limit, with the state persisted in between (部署方案 A8):
#   prescreen    one batch of PRESCREEN_BATCH full texts downloaded and screened (large targets take several steps)
#   orchestrate  one orchestrator turn (one pro call + the tools it calls, e.g. one dispatch round of parallel workers)
#   overflow     batch judgement of candidates no worker reached
#   verify       re-check of every "superseded" verdict, then the result
#   done         self.final / state["result"] holds the final result
PHASES = ("prescreen", "orchestrate", "overflow", "verify", "done")


class Orchestra:
    def __init__(self, target: dict, worker_tools=None, backend_factory=llm.pro, since: str | None = None,
                 exclude: set[str] | frozenset = frozenset(), budget: int = config.ORCH_BUDGET, on_event=None,
                 access=None, state: dict | None = None, quantity_backend=None):
        """access: citations.access.LocalCitationAccess (default) or McpCitationAccess -- every Europe PMC operation.
        state: a dict from to_state() to resume a stepwise run (no pre-screen then).
        on_event(dict): optional progress callback (orchestrator decisions, worker tool calls and results, verification);
        used by the Playground to show the run live. Never affects the result."""
        from .access import LocalCitationAccess
        self.on_event = on_event
        self.t = target
        self.backend_factory = backend_factory
        self.budget = budget
        self.access = access or LocalCitationAccess(target)
        self.worker_tools = worker_tools or self.access.worker_tools()
        self.lock = threading.Lock()
        if state is not None:
            self._load(state)
            return
        self.terms = list(target["terms"])
        self.since, self.exclude = since, set(exclude)
        if "quantity" not in self.t:  # what the old value measures (one flash call); the access objects read it from the target
            self.t["quantity"] = quantity_context(self.t, quantity_backend)
        self._emit(kind="quantity", quantity=self.t["quantity"])
        self._emit(kind="prefetch_start")
        hits, self.prefetch_stats = self.access.list_citers(self.terms, since, exclude)
        # Best effort within PRESCREEN_MAX_WORKS: papers citing the preprint itself first (they read the v1 claim), then
        # oldest first (cited before the published version appeared). Anything beyond the limit is reported, not dropped
        # silently (prefetch_stats.screen_capped -> coverage_notes).
        order = sorted(hits, key=lambda w: ("preprint" not in (hits[w].get("cites_target_version") or []), str(hits[w].get("date") or ""), w))
        self.pending = [[w, hits[w]] for w in order[:config.PRESCREEN_MAX_WORKS]]
        self.prefetch_stats.update(screen_capped=max(0, len(order) - config.PRESCREEN_MAX_WORKS), screen_limit=config.PRESCREEN_MAX_WORKS,
                                   screened=0, no_full_text=0, no_sentence=0, secs=0.0)
        self.cands: dict[str, dict] = {}
        self.verdicts: dict[str, dict] = {}
        self.rounds = self.hops = 0
        self.usage = Usage()
        self.events: list[dict] = [{"quantity": self.t["quantity"]}]
        self.msgs: list[dict] = []
        self.phase, self.turns, self.summary, self.orchestrate_s = "prescreen", 0, "", 0.0
        self.by_workers = self.n_overflow = 0
        self.dispatched: set[str] = set()  # every work_id ever sent to a worker (counts against WORKER_CAPACITY)
        self.final: dict | None = None
        self.later_truncated: list[str] = []  # search_more / follow_chain searches that hit the page limit

    # ---------------------------------------------------------------- state (stepwise runs)
    def to_state(self) -> dict:
        return {"target": self.t, "terms": self.terms, "since": self.since, "exclude": sorted(self.exclude),
                "cands": self.cands, "prefetch_stats": self.prefetch_stats, "verdicts": self.verdicts,
                "rounds": self.rounds, "hops": self.hops, "usage": {"calls": self.usage.calls, "tokens": self.usage.tokens},
                "events": self.events, "msgs": llm.dump_messages(self.msgs), "phase": self.phase, "turns": self.turns,
                "budget": self.budget, "summary": self.summary, "orchestrate_s": self.orchestrate_s,
                "by_workers": self.by_workers, "n_overflow": self.n_overflow, "result": self.final,
                "pending": self.pending, "later_truncated": self.later_truncated, "dispatched": sorted(self.dispatched)}

    def _load(self, s: dict) -> None:
        self.t = s["target"]
        self.terms, self.since, self.exclude = list(s["terms"]), s.get("since"), set(s.get("exclude") or ())
        self.cands, self.prefetch_stats, self.verdicts = s["cands"], s["prefetch_stats"], s["verdicts"]
        self.rounds, self.hops = s["rounds"], s["hops"]
        self.usage = Usage(s["usage"]["calls"], s["usage"]["tokens"])
        self.events, self.msgs = s["events"], llm.load_messages(s["msgs"])
        self.phase, self.turns, self.budget, self.summary = s["phase"], s["turns"], s.get("budget", self.budget), s["summary"]
        self.orchestrate_s, self.by_workers, self.n_overflow = s["orchestrate_s"], s["by_workers"], s["n_overflow"]
        self.final = s.get("result")
        self.pending, self.later_truncated = s.get("pending") or [], s.get("later_truncated") or []
        # states saved before 2026-10-03 have no "dispatched": treat every judged paper as sent
        self.dispatched = set(s["dispatched"]) if "dispatched" in s else set(self.verdicts)

    def _emit(self, **event) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 -- display only
                pass

    @property
    def old(self) -> str:
        return self.t["drift"]["preprint_v1_claim"]

    @property
    def new(self) -> str:
        return self.t["drift"]["current_claim"]

    def _fmt(self, s: str, **kw) -> str:
        return s.format(author=self.t.get("first_author") or "the", old=self.old, new=self.new, **kw)

    # ---------------------------------------------------------------- orchestrator tools
    def overview(self, page: int = 1, only_unjudged: bool = False) -> dict:
        rows = [{"work_id": w, "date": c.get("date"), "flags": c.get("flags"), "source": c["source"],
                 "judged": self.verdicts.get(w, {}).get("cites"), "first_sentence": (c.get("sentences") or [""])[0][:160]}
                for w, c in self.cands.items()]
        if only_unjudged:
            rows = [r for r in rows if not r["judged"]]
        ps = config.OVERVIEW_PAGE_SIZE
        n_pages = max(1, -(-len(rows) // ps))
        page = min(max(1, int(page or 1)), n_pages)
        return {"n_candidates": len(self.cands), "n_judged": sum(1 for w in self.cands if w in self.verdicts),
                "n_unjudged": sum(1 for w in self.cands if w not in self.verdicts), "rounds_used": self.rounds,
                "hops_used": self.hops, "page": page, "n_pages": n_pages, "page_size": ps,
                "candidates": rows[(page - 1) * ps: page * ps]}

    def _worker(self, group: list[str], idx: int = 0) -> list[dict]:
        backend = self.backend_factory()
        t0 = time.time()
        self._emit(kind="worker_start", worker=idx, papers=list(group))

        def call(name: str, args: dict):
            self._emit(kind="worker_tool", worker=idx, tool=name, work_id=(args or {}).get("work_id"))
            return self.worker_tools.call(name, args)

        papers = []
        for w in group:
            c = self.cands.get(w, {})
            papers.append(f"- {w} (flags: {', '.join(c.get('flags') or []) or 'none'}; "
                          f"pre-screen sentence: {(c.get('sentences') or [''])[0][:300]})")
        try:
            verdicts = run_worker(self.t, papers, self.worker_tools, backend, call)
        except Exception as e:  # noqa: BLE001 -- one failed worker must not end the run; its papers stay unjudged
            self._emit(kind="worker_failed", worker=idx, error=f"{type(e).__name__}: {e}"[:300])
            with self.lock:
                self.events.append({"worker_failed": {"papers": list(group), "error": f"{type(e).__name__}: {e}"[:300]}})
            verdicts = []
        self.usage.add("workers", backend)
        self._emit(kind="worker_done", worker=idx, secs=round(time.time() - t0, 1),
                   verdicts=[v.get("cites") for v in verdicts if isinstance(v, dict)])
        return verdicts

    def dispatch(self, groups: list) -> dict:
        if self.rounds >= config.ORCH_MAX_ROUNDS:
            return {"error": f"dispatch limit reached ({config.ORCH_MAX_ROUNDS})"}
        groups = [[w for w in g if w in self.cands][:config.ORCH_MAX_PAPERS] for g in groups if isinstance(g, list)][:config.ORCH_MAX_WORKERS]
        groups = [g for g in groups if g]
        room = config.WORKER_CAPACITY - len(self.dispatched)
        groups = self._fit(groups, room)
        if not groups:
            return {"error": "no known candidate work_ids in groups" if room > 0 else f"worker capacity used up ({config.WORKER_CAPACITY} papers)"}
        self.rounds += 1
        t0 = time.time()
        self._emit(kind="dispatch", round=self.rounds, workers=len(groups), papers=sum(map(len, groups)))
        results, new = self._run_workers(groups, dispatched_by="orchestrator", round_=self.rounds)
        self.events.append({"dispatch": self.rounds, "workers": len(groups), "papers": sum(map(len, groups)), "secs": round(time.time() - t0, 1)})
        return {"workers": len(groups), "verdicts_returned": new, "secs": round(time.time() - t0, 1),
                "capacity_left": config.WORKER_CAPACITY - len(self.dispatched),
                "summary": [{"work_id": v.get("work_id"), "cites": v.get("cites"), "role": v.get("role"), "relayed_by": v.get("relayed_by", "")}
                            for vs in results for v in vs if isinstance(v, dict)]}

    @staticmethod
    def _fit(groups: list[list[str]], room: int) -> list[list[str]]:
        """Trim groups to the remaining worker capacity (papers)."""
        out = []
        for g in groups:
            if room <= 0:
                break
            out.append(g[:room])
            room -= len(out[-1])
        return out

    def _run_workers(self, groups: list[list[str]], dispatched_by: str, round_: int | None = None) -> tuple[list, int]:
        """Run the groups in parallel workers; record verdicts (one per paper, the first one wins)."""
        for g in groups:
            self.dispatched.update(g)
        with cf.ThreadPoolExecutor(len(groups)) as ex:
            results = list(ex.map(self._worker, groups, range(1, len(groups) + 1)))
        new = 0
        for vs in results:
            for v in vs:
                if isinstance(v, dict) and v.get("work_id") in self.cands:
                    self.verdicts[v["work_id"]] = {**v, "judged_by": "worker", "dispatched_by": dispatched_by,
                                                   **({"dispatch_round": round_} if round_ else {})}
                    new += 1
        return results, new

    def auto_dispatch(self) -> bool:
        """One step of the program's dispatch of leftovers: candidates never sent to a worker, in candidate order, up to
        ORCH_MAX_WORKERS groups of ORCH_MAX_PAPERS and within WORKER_CAPACITY. Returns True when more steps are needed."""
        room = config.WORKER_CAPACITY - len(self.dispatched)
        left = [w for w in self.cands if w not in self.verdicts and w not in self.dispatched]
        if room <= 0 or not left:
            return False
        take = left[:min(room, config.ORCH_MAX_WORKERS * config.ORCH_MAX_PAPERS)]
        groups = [take[i:i + config.ORCH_MAX_PAPERS] for i in range(0, len(take), config.ORCH_MAX_PAPERS)]
        t0 = time.time()
        self._emit(kind="auto_dispatch", workers=len(groups), papers=len(take))
        _, new = self._run_workers(groups, dispatched_by="program")
        self.n_overflow += len(take)
        self.events.append({"auto_dispatch": {"workers": len(groups), "papers": len(take), "verdicts_returned": new,
                                              "secs": round(time.time() - t0, 1)}})
        room -= len(take)
        return room > 0 and any(w not in self.verdicts and w not in self.dispatched for w in self.cands)

    def search_more(self, terms: str) -> dict:
        proposed = [x.strip() for x in str(terms).split(",") if x.strip()]
        # The pre-screen already searched every rule-generated spelling (terms.query_variants). A proposed spelling is
        # searched as it is; each match is read as the whole quantity it belongs to and counts only when that quantity
        # equals the old value, with the measured property in the same sentence or table row (quantity["proposed"],
        # terms.quantity_sentences) -- the same quantity match as the pre-screen, no separate rule for spellings.
        q = self.t.get("quantity") or {"property_terms": [], "unit": None}
        self.t["quantity"] = q
        searched = any_regex(self.terms)
        covered = [x for x in proposed if searched.search(x)]  # contains a searched spelling
        new_terms = [x for x in proposed if x not in covered]
        out = {"already_searched": covered}
        if new_terms:
            q["proposed"] = list(q.get("proposed") or []) + [x for x in new_terms if x not in (q.get("proposed") or [])]
        if not new_terms:
            self._emit(kind="search_more", terms=[], added=0, covered=covered)
            return {**out, "new_candidates": 0, "n_candidates": len(self.cands)}
        found, truncated = self.access.search_more(new_terms, self.since, known=set(self.cands) | self.exclude)
        if truncated:
            self.later_truncated.append(f"search_more {','.join(new_terms)}")
        found = {w: c for w, c in found.items() if w not in self.cands}
        self.cands.update(found)
        self.terms += [t for t in new_terms if t not in self.terms]
        self._emit(kind="search_more", terms=new_terms, added=len(found), covered=covered)
        return {**out, "new_candidates": len(found), "n_candidates": len(self.cands)}

    def follow_chain(self, intermediary: str) -> dict:
        if self.hops >= config.ORCH_MAX_HOPS:
            return {"error": f"hop limit reached ({config.ORCH_MAX_HOPS})"}
        r = self.access.follow_chain(intermediary, self.terms, self.since, known=set(self.cands) | self.exclude)
        if "error" in r:
            return r
        self.hops += 1
        if r.get("truncated"):
            self.later_truncated.append(f"follow_chain {r['intermediary'][:60]}")
        found = {w: c for w, c in r["cands"].items() if w not in self.cands}
        self.cands.update(found)
        self.events.append({"hop": self.hops, "intermediary": r["intermediary"][:90], "added": len(found)})
        self._emit(kind="follow_chain", intermediary=r["intermediary"][:90], added=len(found))
        return {"intermediary": r["intermediary"], "new_candidates": len(found), "n_candidates": len(self.cands)}

    # ---------------------------------------------------------------- P1.2 verification of "superseded"
    def _evidence(self, wid: str) -> dict:
        v = self.verdicts[wid]
        ev = self.access.evidence(wid, self.terms, str(v.get("sentence") or ""))
        return {"work_id": wid, "reference_matched": ev.get("reference_matched"),
                "sentences_citing_target": ev.get("sentences_citing_target") or [],
                "sentences_with_old_value": ev.get("sentences_with_old_value") or [], "flags": self.cands.get(wid, {}).get("flags"),
                "worker_verdict": {k: v.get(k) for k in ("cites", "role", "sentence", "relayed_by", "reason")},
                "worker_sentence_verbatim": ev.get("worker_sentence_verbatim")}

    def _verify_group(self, wids: list[str]) -> list[dict]:
        backend = self.backend_factory()
        ev = [self._evidence(w) for w in wids]
        r = backend.chat([{"role": "system", "content": self._fmt(VERIFY_SYSTEM)},
                          {"role": "user", "content": json.dumps(ev, ensure_ascii=False)}])
        self.usage.add("verifier", backend)
        return (llm.extract_json(r["content"]) or {}).get("checks") or []

    def verify_superseded(self, group_size: int = 5) -> dict:
        wids = [w for w, v in self.verdicts.items() if v.get("cites") == "superseded"]
        groups = [wids[i:i + group_size] for i in range(0, len(wids), group_size)]
        self._emit(kind="verify_start", papers=len(wids), calls=len(groups))
        changed = 0
        if groups:
            with cf.ThreadPoolExecutor(min(len(groups), config.ORCH_MAX_WORKERS)) as ex:
                checks = [c for cs in ex.map(self._verify_group, groups) for c in cs if isinstance(c, dict)]
        else:
            checks = []
        replace = []
        for c in checks:
            w = c.get("work_id")
            if w not in self.verdicts or self.verdicts[w].get("cites") != "superseded":
                continue
            v = self.verdicts[w]
            v["verification"] = {k: c.get(k) for k in ("verdict", "condition_1", "condition_2", "condition_3", "reason")}
            if c.get("verdict") in VERDICTS - {"superseded"}:
                v["verdict_before_verification"] = {k: v.get(k) for k in ("cites", "role", "sentence")}
                v["cites"] = c["verdict"]
                for k in ("relayed_by", "unclear_rule"):
                    if c.get(k):
                        v[k] = c[k]
                changed += 1
            if c.get("sentence"):
                replace.append((w, c["sentence"]))
        # the verifier's sentence replaces the worker's only when the worker's is not verbatim (re-checked in result())
        found = self.access.quotes_found([{"work_id": w, "quote": str(self.verdicts[w].get("sentence") or "")} for w, _ in replace])
        for (w, sent), ok in zip(replace, found):
            if not ok:
                self.verdicts[w]["sentence"] = sent
        unchecked = [w for w in wids if "verification" not in self.verdicts[w]]
        for w in unchecked:  # verifier returned nothing for it: do not let an unchecked "superseded" through silently
            self.verdicts[w]["verification"] = {"verdict": None, "reason": "verifier returned no check"}
        res = {"checked": len(wids) - len(unchecked), "changed": changed, "unchecked": unchecked}
        self.events.append({"verification": res})
        self._emit(kind="verify_done", checked=res["checked"], changed=changed)
        return res

    # ---------------------------------------------------------------- run
    def _orchestrator_turn(self) -> None:
        """One orchestrator turn; moves to the overflow phase when the orchestrator finishes or the budget is spent."""
        if not self.cands:
            self.summary, self.phase = "no candidates after pre-screening", "overflow"
            return
        t0 = time.time()
        backend = self.backend_factory()
        fns = {"overview": self.overview, "dispatch": self.dispatch, "search_more": self.search_more, "follow_chain": self.follow_chain}
        self._emit(kind="orchestrator_thinking")
        r = backend.chat(self.msgs, tools=ORCH_TOOL_SCHEMAS)
        self.turns += 1
        self.msgs.append(r["assistant_message"])
        for c in r["tool_calls"]:
            self._emit(kind="orchestrator", tool=c["name"], args=c["args"] or {})
        done = not r["tool_calls"]
        if done:
            self.summary = r["content"]
        for c in r["tool_calls"]:
            if c["name"] == "finish":
                self.summary, done, out = (c["args"] or {}).get("summary", ""), True, {"ok": True}
            else:
                try:
                    out = fns[c["name"]](**(c["args"] or {}))
                except Exception as e:  # noqa: BLE001
                    out = {"error": f"{type(e).__name__}: {e}"}
            self.events.append({"orchestrator_call": c["name"], "args": c["args"]})
            self.msgs.append(backend.tool_result_message(c["name"], json.dumps(out, ensure_ascii=False)[:12000]))
        self.usage.add("orchestrator", backend)
        self.orchestrate_s += time.time() - t0
        if done or self.turns >= self.budget:
            self.phase = "overflow"

    def _prescreen_batch(self) -> None:
        t0 = time.time()
        batch, self.pending = self.pending[:config.PRESCREEN_BATCH], self.pending[config.PRESCREEN_BATCH:]
        if batch:
            found, counts = self.access.screen(dict(batch), self.terms)
            self.cands.update(found)
            for k, v in counts.items():
                self.prefetch_stats[k] = self.prefetch_stats.get(k, 0) + v
        self.prefetch_stats["secs"] = round(self.prefetch_stats.get("secs", 0) + time.time() - t0, 1)
        self._emit(kind="prescreen_batch", screened=self.prefetch_stats["screened"], remaining=len(self.pending),
                   candidates=len(self.cands))
        if self.pending:
            return
        self.prefetch_stats["with_full_text_and_sentence"] = len(self.cands)
        self._emit(kind="prefetch_done", candidates=len(self.cands))
        self.msgs = [
            {"role": "system", "content": self._fmt(ORCH_SYSTEM, ps=config.OVERVIEW_PAGE_SIZE, mw=config.ORCH_MAX_WORKERS,
                                                    mp=config.ORCH_MAX_PAPERS, mr=config.ORCH_MAX_ROUNDS, mh=config.ORCH_MAX_HOPS,
                                                    cap=config.WORKER_CAPACITY)},
            {"role": "user", "content": f"Target: {self.t.get('first_author')} et al. {len(self.cands)} candidates after pre-screening. Start with overview()."}]
        self.phase = "orchestrate"

    def step(self) -> str:
        """Run one unit of work (see PHASES); returns the new phase."""
        if self.phase == "prescreen":
            self._prescreen_batch()
        elif self.phase == "orchestrate":
            self._orchestrator_turn()
        elif self.phase == "overflow":  # leftovers: the program dispatches them to workers within the capacity
            if not self.n_overflow:
                self.by_workers = len(self.verdicts)
            if not self.auto_dispatch():
                self.phase = "verify"
        elif self.phase == "verify":
            self.verify_superseded()
            self.final = self.result(self.summary, self.orchestrate_s, self.by_workers, self.n_overflow)
            self.phase = "done"
        return self.phase

    def run(self) -> dict:
        while self.phase != "done":
            self.step()
        return self.final

    def coverage(self, unjudged: list) -> tuple[dict, list[str]]:
        """What this run could and could not look at. `truncated` = something within reach was NOT examined (search cut
        off, screening limit, unjudged candidates); papers without an open full text are outside the method's reach and
        are reported but do not count as truncation."""
        ps = self.prefetch_stats or {}
        per = ps.get("per_version") or {}
        hit_count = sum(v.get("hit_count", 0) for v in per.values())
        fetched = sum(v.get("fetched", 0) for v in per.values())
        cov = {"europe_pmc_hits": hit_count, "retrieved": fetched, "no_open_full_text": ps.get("no_open_full_text", 0),
               "matching_papers": ps.get("citing_works_with_value"), "screened": ps.get("screened"),
               "screen_capped": ps.get("screen_capped", 0), "download_failed": ps.get("no_full_text", 0),
               "candidates": len(self.cands), "judged": len(self.cands) - len(unjudged), "unjudged": len(unjudged),
               "later_searches_truncated": list(self.later_truncated)}
        notes = []
        if ps.get("search_truncated"):
            notes.append(f"Europe PMC reported {hit_count} citing papers containing the old value, but only the first {fetched} "
                         f"could be retrieved (page limit); the rest were not examined.")
        if cov["screen_capped"]:
            notes.append(f"{cov['screen_capped']} of {ps.get('citing_works_with_value')} matching papers were not screened "
                         f"(limit {ps.get('screen_limit')} per run; papers citing the preprint and older papers were screened first).")
        for x in self.later_truncated:
            notes.append(f"The search of {x} hit the page limit; part of its results was not examined.")
        cut = [u for u in unjudged if u["work_id"] not in self.dispatched]
        if cut:
            notes.append(f"{len(cut)} candidate(s) were not judged: the run's worker capacity ({config.WORKER_CAPACITY} papers) "
                         f"was used up.")
        if len(unjudged) > len(cut):
            notes.append(f"{len(unjudged) - len(cut)} candidate(s) were sent to a worker but got no verdict.")
        if cov["download_failed"]:
            notes.append(f"{cov['download_failed']} matching paper(s) had no downloadable full text and could not be read.")
        if cov["no_open_full_text"]:
            notes.append(f"{cov['no_open_full_text']} citing paper(s) matched only in metadata without an open full text in "
                         f"Europe PMC (outside the method's reach).")
        cov["truncated"] = bool(ps.get("search_truncated") or cov["screen_capped"] or self.later_truncated or unjudged)
        return cov, notes

    def result(self, summary: str, orchestrate_s: float, by_workers: int, n_overflow: int) -> dict:
        items = list(self.verdicts.items())
        verified = self.access.quotes_found([{"work_id": w, "quote": str(v.get("sentence") or "")} for w, v in items])
        works = []
        for (w, v), sentence_ok in zip(items, verified):
            c = self.cands.get(w, {})
            cites = v.get("cites") if v.get("cites") in VERDICTS else "unclear"
            works.append({"work_id": w, "title": c.get("title"), "date": c.get("date"), "journal": c.get("journal"),
                          "authors": c.get("authors"), "doi": c.get("doi"), "cites": cites,
                          "role": v.get("role") if v.get("role") in ROLES else "unknown",
                          "sentence": v.get("sentence") or "", "sentence_verified": sentence_ok,
                          "relayed_by": v.get("relayed_by") or "", "unclear_rule": v.get("unclear_rule") or "",
                          "reason": v.get("reason") or "", "flags": c.get("flags") or [], "found_via": c.get("source"),
                          "cites_versions": c.get("cites_versions") or [], "judged_by": v.get("judged_by"),
                          "verification": v.get("verification"), "verdict_before_verification": v.get("verdict_before_verification"),
                          "label_problem": None if v.get("cites") in VERDICTS else f"invalid class {v.get('cites')!r}"})
        unjudged = [{"work_id": k, "flags": c.get("flags"), "source": c["source"],
                     "reason": "no verdict returned" if k in self.dispatched else "worker capacity used up"}
                    for k, c in self.cands.items() if k not in self.verdicts]
        coverage, notes = self.coverage(unjudged)
        return {"target_id": self.t.get("target_id"), "drift_event_id": self.t.get("drift_event_id"), "mode": "orchestra",
                "terms": self.terms, "since": self.since, "prefetch": self.prefetch_stats, "orchestrate_s": round(orchestrate_s, 1),
                "calls": self.usage.calls, "tokens": self.usage.tokens, "n_candidates": len(self.cands),
                "n_judged": len(self.cands) - len(unjudged), "n_unjudged": len(unjudged),
                "coverage_complete": not unjudged and not coverage["truncated"], "coverage": coverage, "coverage_notes": notes,
                "unjudged": unjudged, "judged_by": {"worker": by_workers, "auto_dispatch": len(self.verdicts) - by_workers},
                "n_auto_dispatched": n_overflow, "worker_capacity": config.WORKER_CAPACITY,
                "n_dispatched": len(self.dispatched), "rounds_used": self.rounds, "hops_used": self.hops,
                "events": self.events, "summary": summary, "citing_works": works}
