"""Stepwise citation analysis (部署方案 A8). One call = one unit of work, the run's state is plain JSON:

  state = start(target, since, exclude)      pre-screen (through the tool service), phase "orchestrate"
  state = step(state)                         one orchestrator turn / the overflow batch / verification + result
  state["phase"] == "done"  ->  state["result"] is what Orchestra.run() returns

The same functions run in-process (local, tests) and inside the citation_finder engine on Agent Engine; the citation
queue job (jobs.py) persists the state in citation_runs between steps, so a failed step is retried from the last
saved state and no single call comes near Agent Engine's request limit.
"""
from __future__ import annotations

from collections import Counter

from .access import LocalCitationAccess, McpCitationAccess


def make_access(target: dict, via: str = "mcp", client=None):
    """via "mcp": the tool service (stdio locally, CLAIMDRIFT_MCP_URL when deployed); "local": in-process."""
    if via == "local":
        return LocalCitationAccess(target)
    return McpCitationAccess(target, client)


def start(target: dict, since: str | None = None, exclude=(), via: str = "mcp", client=None, on_event=None) -> dict:
    from .orchestra import Orchestra
    o = Orchestra(target, access=make_access(target, via, client), since=since, exclude=set(exclude or ()), on_event=on_event)
    return o.to_state()


def step(state: dict, via: str = "mcp", client=None, on_event=None) -> dict:
    from .orchestra import Orchestra
    o = Orchestra(state["target"], access=make_access(state["target"], via, client), on_event=on_event, state=state)
    o.step()
    return o.to_state()


def progress(state: dict) -> dict:
    """Small progress record of a run (saved in citation_runs.progress after every step; the UI shows it live)."""
    ps = state.get("prefetch_stats") or {}
    screened = ps.get("screened", 0) or 0
    return {"phase": state.get("phase"), "screened": screened, "to_screen": screened + len(state.get("pending") or []),
            "candidates": len(state.get("cands") or {}), "judged": len(state.get("verdicts") or {}),
            "orchestrator_turns": state.get("turns", 0), "line": progress_line(state)}


def progress_line(state: dict) -> str:
    n_c, n_j = len(state.get("cands") or {}), len(state.get("verdicts") or {})
    ps = state.get("prefetch_stats") or {}
    return {"prescreen": f"pre-screen: {ps.get('screened', 0)}/{ps.get('screened', 0) + len(state.get('pending') or [])} full texts screened, {n_c} candidate(s)",
            "orchestrate": f"orchestrator turn {state.get('turns', 0)}: {n_c} candidate(s), {n_j} judged",
            "overflow": f"orchestrator finished: {n_c} candidate(s), {n_j} judged",
            "verify": f"overflow judged: {n_j}/{n_c}",
            "done": "verification done"}.get(state.get("phase"), "")


def describe_event(ev: dict) -> str | None:
    """One readable log line per internal step of the citation orchestra (Playground node log)."""
    k = ev.get("kind")
    if k == "prefetch_start":
        return "pre-screen: searching Europe PMC for citing papers that contain the old value"
    if k == "prescreen_batch":
        return f"pre-screen: {ev['screened']} screened, {ev['remaining']} to go, {ev['candidates']} candidate(s)"
    if k == "prefetch_done":
        return f"pre-screen: {ev['candidates']} candidate citing paper(s)"
    if k == "orchestrator_thinking":
        return "orchestrator (pro): deciding the next step..."
    if k == "orchestrator":
        a, t = ev.get("args") or {}, ev.get("tool")
        if t == "dispatch":
            gs = [g for g in a.get("groups") or [] if isinstance(g, list)]
            return f"orchestrator -> dispatch {len(gs)} group(s), {sum(len(g) for g in gs)} paper(s)"
        if t == "finish":
            return "orchestrator -> finish: " + str(a.get("summary") or "")[:160]
        return f"orchestrator -> {t}(" + ", ".join(f"{x}={y}" for x, y in a.items())[:120] + ")"
    if k == "dispatch":
        return f"round {ev['round']}: {ev['workers']} worker(s) in parallel on {ev['papers']} paper(s)"
    if k == "worker_start":
        return f"worker {ev['worker']}: reading " + ", ".join(ev["papers"])
    if k == "worker_tool":
        return f"worker {ev['worker']}: {ev['tool']}({ev.get('work_id') or ''})"
    if k == "worker_done":
        c = Counter(v or "invalid" for v in ev.get("verdicts") or [])
        return f"worker {ev['worker']} done in {ev['secs']}s: " + (", ".join(f"{n} {v}" for v, n in c.most_common()) or "no verdicts")
    if k == "search_more":
        return f"search_more {ev['terms']}: {ev['added']} new candidate(s)"
    if k == "follow_chain":
        return f"follow_chain via '{ev['intermediary']}': {ev['added']} new candidate(s)"
    if k == "verify_start":
        return f"verification (pro): re-checking {ev['papers']} 'superseded' verdict(s) in {ev['calls']} call(s)"
    if k == "verify_done":
        return f"verification: {ev['checked']} checked, {ev['changed']} verdict(s) changed"
    if k == "batch_start":
        return f"overflow: batch-judging {ev['papers']} paper(s) no worker reached"
    return None
