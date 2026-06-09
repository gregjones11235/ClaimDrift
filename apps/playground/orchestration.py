"""ClaimDrift 5-Agent Orchestration Playground backend.

The SECOND playground experiment (the first is the A/B memory experiment in
server.py). One SSE endpoint that runs the REAL, full supervisor pipeline LIVE
for a judge pressing a single button, and lights up the 5-agent fan-out
node-by-node as each sub-agent reports in:

  GET /api/playground/orchestrate?email=<judge-email>  (text/event-stream)

  claim_extractor ×2 (parallel) -> drift_analyzer -> citation_finder
    -> notifier ×N (parallel) -> memory_synthesizer

WHY THE FULL SUPERVISOR (unlike the A/B experiment, which runs ONLY
drift_analyzer): this experiment's entire point is to SHOW the orchestration —
how the 5 agents fan out and chain. So it drives the deployed supervisor
reasoning engine (id 7816826734824652800) directly and streams every sub-agent
event, translated into node-lighting SSE frames.

It really runs ~200s and really sends mail (the judge enters their own email
and receives the drift alert) — both are features, not bugs.

THE HARD PART — teardown isolation (so repeated demo runs never accumulate
duplicate/inflated patterns and never corrupt the A/B experiment's real pattern
library): the full supervisor run has TWO write side effects on shared
production indices —
  1. supervisor mints ONE drift_event (random uuid, agent.py:312),
  2. memory_synthesizer writes ONE pattern via create_drift_pattern (new doc)
     OR update_drift_pattern (mutates an EXISTING real pattern by appending the
     new event_id to source_event_ids).
Neither doc carries a demo `record_source` tag (the create workflow
intentionally omits it; the pattern _id is a random LLM-minted uuid), so the
E2 tag-based _cleanup cannot find them. AND production's scheduled workflow may
write to the SAME drift_patterns index concurrently during our ~200s window.

So teardown is PRECISE-ID-ONLY — it touches ONLY the exact event_id and
pattern_id THIS run wrote, captured from the live stream. There is deliberately
NO full-index diff / snapshot-restore: that would mis-delete a real pattern the
production workflow wrote concurrently. If an id can't be captured we emit a
warning and leave the doc for manual cleanup rather than ever batch-deleting.

  - drift_event: DELETE by the captured event_id.
  - pattern, create case: DELETE the new pattern by id.
  - pattern, update case: the update workflow is append-evidence-only set-union,
    so the EXACT inverse is to remove our one captured event_id from
    source_event_ids (painless), recompute support_count, refresh timestamp.
    We do NOT overwrite the whole _source, so a concurrent production update to
    the same pattern (a DIFFERENT event_id) is left intact.

Run locally (WSL):
    cd agents && uv run uvicorn apps.playground.server:app --port 8799
    curl -N "http://127.0.0.1:8799/api/playground/orchestrate?email=you@example.com"
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from email.mime.text import MIMEText
from typing import Any, AsyncIterator

from ingestion.common.elastic import ElasticsearchHttpClient
from _shared.elastic_retrieval import DRIFT_PATTERNS_INDEX

# Top-level import (NOT lazy). On Cloud Run's CPU-throttled request path,
# `import vertexai` alone took the lion's share of a measured 82-124s get_engine
# (vs ~2s locally) — it's a huge package and pure-CPU bytecode parse. Doing it
# at MODULE LOAD means it runs during container startup, which gets full CPU
# (startup-cpu-boost) and, with --no-cpu-throttling, stays off the request path
# entirely. Safe at top level because the deployed container always has the
# vertexai dependency (the lazy-import dance only mattered for credential-free
# local test scaffolds, which don't import this module).
import vertexai
from vertexai import agent_engines

# Same physical event index the E2 experiment and dispatcher use.
DRIFT_EVENTS_INDEX = "drift_events"

# Supervisor reasoning engine (Phase 5f). Stable resource id; see
# agents/supervisor_agent/scripts/capture_stream.py:33 and _DEPLOY_CHECKLIST.md.
SUPERVISOR_ENGINE_ID = "7816826734824652800"

# Sub-agent reasoning engines that call gemini-2.5-pro and can therefore hit a
# 429 Dynamic-Shared-Quota wall that empties the run. Verified 2026-06-08 via
# vertexai.agent_engines.list(): BOTH claim_extractor (runs FIRST, so its 429
# stalls the chain before drift_analyzer is even reached) AND drift_analyzer
# throw RESOURCE_EXHAUSTED under regional peak load. We confirm a quota failure
# by reading EITHER engine's logs — checking only drift_analyzer (the old bug)
# missed claim_extractor-stage 429s, which then showed as "suspected" forever.
SUPERVISOR_SUB_AGENT_429_ENGINES = {
    "claim_extractor": "2286406392413683712",
    "drift_analyzer": "5333654490283245568",
}

# Keep the legacy single-engine alias for drift_analyzer, so the old name still
# resolves for callers (the Experiment-1 A/B path in server.py imports it).
SUPERVISOR_SUB_AGENT_DRIFT_ID = SUPERVISOR_SUB_AGENT_429_ENGINES["drift_analyzer"]

# ALL gemini-2.5-pro sub-agents, keyed by node name → reasoning engine id. This
# is a SUPERSET of SUPERVISOR_SUB_AGENT_429_ENGINES: it additionally includes
# memory_synthesizer (engine 8580327609152307200), which the empty-run quota
# check above deliberately omits.
#
# Why two lists, not one:
#   - SUPERVISOR_SUB_AGENT_429_ENGINES drives the EMPTY-RUN detection
#     (orchestration.py ~868): a run where drift_analyzer produced nothing.
#     memory_synthesizer runs at the TAIL, AFTER drift_analyzer; an empty run
#     never reached it, so adding it there would only risk mis-attributing a
#     stale prior-run 429 to the current empty run.
#   - PRO_SUB_AGENT_ENGINES drives PER-NODE error confirmation in
#     _friendly_node_error: when a tail/non-blocking node fails with a 429's
#     SECONDARY symptom (the deployed engine 429s INSIDE its second LLM call, so
#     the supervisor only sees "schema validation failed … 'action' is a
#     required property", never the 429 itself), we look up THAT node's engine
#     and confirm the real cause in its logs. memory_synthesizer is the case
#     that bit us 2026-06-09 (15× 429 / 0 successful outputs in 6h), so it MUST
#     be reachable here.
PRO_SUB_AGENT_ENGINES = {
    **SUPERVISOR_SUB_AGENT_429_ENGINES,
    "memory_synthesizer": "8580327609152307200",
}

log = logging.getLogger("claimdrift.playground.orchestration")


def _count_recent_429(engine_id: str, within_s: int = 180) -> int:
    """Count RESOURCE_EXHAUSTED / 429 log entries for ONE reasoning engine in
    the last `within_s` seconds, to CONFIRM (not guess) that an empty run was a
    gemini-2.5-pro Dynamic-Shared-Quota hit. Returns 0 on any logging-API error
    (callers then say 'suspected'). SYNCHRONOUS — callers offload via to_thread.
    Used by the Experiment-1 A/B path (server.py) for drift_analyzer alone."""
    try:
        from datetime import datetime, timedelta, timezone

        from google.cloud import logging as gcl

        client = gcl.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"])
        since = (datetime.now(timezone.utc) - timedelta(seconds=within_s)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        flt = (
            'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
            f'resource.labels.reasoning_engine_id="{engine_id}" '
            '(textPayload:"RESOURCE_EXHAUSTED" OR textPayload:"429") '
            f'timestamp>="{since}"'
        )
        return sum(1 for _ in client.list_entries(filter_=flt, max_results=5))
    except Exception:  # noqa: BLE001 — confirmation is best-effort
        return 0


def _which_recent_429(within_s: int = 180) -> list[str]:
    """Return the names of the gemini-2.5-pro sub-agent engines that logged a
    429 / RESOURCE_EXHAUSTED in the last `within_s` seconds, to CONFIRM (not
    guess) that an empty run was a Dynamic-Shared-Quota hit AND to attribute it
    to the right node (claim_extractor vs drift_analyzer). Returns [] on any
    logging-API error (callers then say 'suspected'). SYNCHRONOUS — callers
    offload via to_thread. Used by Experiment-2 (the 5-agent orchestration),
    which can stall on EITHER sub-agent. Widened to claim_extractor 2026-06-08."""
    hit: list[str] = []
    for name, engine_id in SUPERVISOR_SUB_AGENT_429_ENGINES.items():
        if _count_recent_429(engine_id, within_s) > 0:
            hit.append(name)
    return hit

# Canonical demo envelope — a REAL preprint→published pair with a REAL,
# verified drift. Replaces the prior synthetic HCQ stub (fabricated DOIs
# 10.1101/2024.01.15.123456 → 10.1016/j.cell.2024.05.001), which OpenAlex 404'd,
# so citation_finder always found N=0 citing works and the notifier node never
# lit up. This pair is real end-to-end:
#
#   preprint  10.1101/2023.01.07.523095  (bioRxiv, "The NO Answer for ASD")
#   published 10.1002/advs.202205783     (Advanced Science)
#
# WHY THIS PAIR (all verified 2026-06-08):
#   - Both DOIs resolve in OpenAlex; the PREPRINT has 13 citing works, ALL with
#     DOIs — so citation_finder returns 13 affected_citations and the notifier
#     fans out 13 lanes and sends 13 real alert emails (the whole point of
#     swapping the case). Verified via api.openalex.org cites: filter.
#   - The drift is REAL, not a published-abstract-missing false positive: the
#     production `preprints` index holds a genuine 1380-char published abstract
#     for this pair (NOT a title placeholder), and the production drift_event
#     scored materiality 0.75 with substantive claim_diffs — a strong causal
#     claim ("treated WT mice with an NO donor, which led to an autism-like
#     phenotype") was REMOVED in publication, and "NO plays a *pathological*
#     role" was softened to "*significant* role". See the drift_events record
#     and the verbatim abstracts below (both pulled from production ES /
#     bioRxiv, unedited).
#
# The abstracts below are the REAL, full published/preprint abstracts (verbatim
# from the production `preprints` index, themselves sourced from bioRxiv and the
# Advanced Science article), so drift_analyzer sees exactly what production saw
# and reproduces the ~0.75 outcome honestly.
ENVELOPE: dict[str, Any] = {
    "preprint": {
        "doi": "10.1101/2023.01.07.523095",
        "version": "v1",
        "title": "The NO Answer for Autism Spectrum Disorder",
        "abstract": (
            "Autism spectrum disorders (ASDs) include a range of developmental "
            "disorders that share a core of neurobehavioral deficits manifested by "
            "abnormalities in social interactions, deficits in communication, "
            "restricted interests, and repetitive behaviors. Several reports showed "
            "that mutations in different high-risk ASD genes, including SHANK3 and "
            "CNTNAP2, lead to ASD. However, to date, the underlying molecular "
            "mechanisms have not been deciphered, and no effective pharmacological "
            "treatment has been established for ASD. Recently, we reported a dramatic "
            "increase of nitric oxide (NO) in ASD mouse models. NO is a "
            "multifunctional neurotransmitter that plays a key role in different "
            "neurological disorders. However, its role in ASD has not yet been "
            "investigated. To reveal the novel molecular, cellular, and behavioral "
            "role of NO in ASD, we conducted multidisciplinary experiments using "
            "cellular and mouse models as well as clinical samples. First, we treated "
            "WT mice with an NO donor, which led to an autism-like phenotype. Next, we "
            "measured and found high levels of nitrosative stress biomarkers in both "
            "the Shank3 and Cntnap2 ASD mouse models. Treating both mouse models with "
            "a selective neuronal NO synthase (nNOS) inhibitor led to a reversal in "
            "the molecular, synaptic, and behavioral ASD phenotypes. Using a primary "
            "neuronal cell culture, we confirmed that NO is specifically involved in "
            "neurons in ASD pathology. Next, using genetic manipulations in the human "
            "SH-SY5Y cell line, we found that nNOS plays a key role in the pathology. "
            "Finally, we examined human plasma samples from 19 low-functioning ASD "
            "patients, compared to 20 typically developed volunteers, and found a "
            "significant elevation in the NO levels in the ASD patients. Furthermore, "
            "using the SNOTRAP technology, which is an innovative mass spectrometric "
            "method to identify the SNO-proteome, we revealed that the complement "
            "systems in the synaptic and neuronal development processes are enriched "
            "in the ASD group. This work indicates, for the first time, that NO plays "
            "a pathological role in ASD development. Our findings will open future and "
            "novel directions to examine NO in diverse mutations on the autism "
            "spectrum as well as other neurodevelopmental disorders and psychiatric "
            "diseases. Most importantly, it suggests a novel treatment strategy for "
            "ASD. Nitric oxide plays a key role in ASD pathology development and "
            "progression, and targeting its production leads to a reversal in the "
            "autistic phenotype."
        ),
        "conclusion": (
            "This work indicates, for the first time, that NO plays a pathological "
            "role in ASD development. Treating WT mice with an NO donor led to an "
            "autism-like phenotype, and targeting NO production reversed the autistic "
            "phenotype — suggesting a novel treatment strategy for ASD."
        ),
    },
    "published": {
        "doi": "10.1002/advs.202205783",
        "version": "published",
        "title": "The NO Answer for Autism Spectrum Disorder",
        "abstract": (
            "Autism spectrum disorders (ASDs) include a wide range of "
            "neurodevelopmental disorders. Several reports showed that mutations in "
            "different high-risk ASD genes lead to ASD. However, the underlying "
            "molecular mechanisms have not been deciphered. Recently, they reported a "
            "dramatic increase in nitric oxide (NO) levels in ASD mouse models. Here, "
            "they conducted a multidisciplinary study to investigate the role of NO in "
            "ASD. High levels of nitrosative stress biomarkers are found in both the "
            "Shank3 and Cntnap2 ASD mouse models. Pharmacological intervention with a "
            "neuronal NO synthase (nNOS) inhibitor in both models led to a reversal of "
            "the molecular, synaptic, and behavioral ASD-associated phenotypes. "
            "Importantly, treating iPSC-derived cortical neurons from patients with "
            "SHANK3 mutation with the nNOS inhibitor showed similar therapeutic "
            "effects. Clinically, they found a significant increase in nitrosative "
            "stress biomarkers in the plasma of low-functioning ASD patients. "
            "Bioinformatics of the SNO-proteome revealed that the complement system is "
            "enriched in ASD. This novel work reveals, for the first time, that NO "
            "plays a significant role in ASD. Their important findings will open novel "
            "directions to examine NO in diverse mutations on the spectrum as well as "
            "in other neurodevelopmental disorders. Finally, it suggests a novel "
            "strategy for effectively treating ASD."
        ),
        "conclusion": (
            "This novel work reveals, for the first time, that NO plays a significant "
            "role in ASD. Pharmacological nNOS inhibition reversed ASD-associated "
            "phenotypes in mouse models and in iPSC-derived patient neurons, "
            "suggesting a novel strategy for effectively treating ASD."
        ),
    },
}

# The pipeline shape sent to the frontend on run.started. fanout=True nodes
# render as stacked lanes (claim_extractor ×2 parallel, notifier ×N parallel).
PIPELINE = [
    {"id": "claim_extractor", "label": "Claim Extractor", "fanout": True},
    {"id": "drift_analyzer", "label": "Drift Analyzer", "fanout": False},
    {"id": "citation_finder", "label": "Citation Finder", "fanout": False},
    {"id": "notifier", "label": "Notifier", "fanout": True},
    {"id": "memory_synthesizer", "label": "Memory Synthesizer", "fanout": False},
]
_NODE_IDS = {p["id"] for p in PIPELINE}

# Gmail OAuth secret names in Secret Manager (verbatim from
# apps/dispatcher/main.py:108-112). Copied rather than imported: dispatcher's
# main.py reads many required env vars at import time and would crash this
# leaner process.
GMAIL_SECRETS = {
    "refresh_token": "gmail-refresh-token",
    "client_id": "gmail-oauth-client-id",
    "client_secret": "gmail-oauth-client-secret",
}


# --- SSE helper -------------------------------------------------------------

def _sse(event: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Event frame (mirrors server.py._sse).

    DIAGNOSTIC (2026-06-08): log every frame at emit time so we can tell, from
    Cloud Run logs, whether the app layer is producing frames promptly (and the
    stall is downstream buffering) or stuck before the first yield.
    """
    log.info("SSE emit event=%s", event)
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _strip_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _parts(chunk: dict) -> list[dict]:
    return ((chunk.get("content") or {}).get("parts")) or []


def _loads_lenient(text: str) -> dict | None:
    """Parse a JSON object out of an agent text part, tolerating LLM
    non-determinism: strip any markdown fence, try the whole string, then fall
    back to the widest brace-delimited substring (first '{' .. last '}'). Same
    rationale as server._loads_lenient — agents occasionally wrap JSON in prose,
    which a strict json.loads rejects."""
    s = _strip_fence(text)
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        try:
            out = json.loads(s[start : end + 1])
            return out if isinstance(out, dict) else None
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _first_json_part(chunk: dict) -> dict | None:
    """Return the first text part of a chunk that parses as a JSON object."""
    for p in _parts(chunk):
        t = p.get("text")
        if not t or not t.strip():
            continue
        out = _loads_lenient(t)
        if out is not None:
            return out
    return None


# --- stream translation: chunk -> node lighting -----------------------------

def _looks_like_pro_quota_symptom(raw: str) -> bool:
    """True when a node's raw error is the SECONDARY symptom of a gemini-2.5-pro
    429, not a real logic bug.

    A deployed pro sub-agent that 429s INSIDE one of its LLM calls never surfaces
    "429" to the supervisor: the LLM step just produces no final §3.x.2 text, so
    the supervisor's schema gate (hardening.py) reports whatever it COULD parse —
    typically the preceding MCP tool result, which is missing the required key.
    For memory_synthesizer that is verbatim:
        "schema validation failed at <root>: 'action' is a required property"
    Empty-output exhaustion ("no parseable §3.x.2 output in events") is the other
    shape. Either is a candidate to confirm against engine logs. We match loosely
    on purpose — confirmation against the real 429 logs is the actual gate; this
    only decides whether confirmation is WORTH attempting."""
    low = raw.lower()
    return (
        "schema validation failed" in low
        or "is a required property" in low
        or "no parseable" in low
        or "resource_exhausted" in low
        or "429" in low
    )


async def _friendly_node_error(node: str, raw: str) -> str:
    """Turn a raw sub-agent error into a short, judge-friendly line.

    Two known degraded-but-not-fatal causes get a human explanation instead of a
    raw red dump; everything else is passed through verbatim so we never hide a
    real failure.

    1. MALFORMED_FUNCTION_CALL: the model occasionally emits its tool call as a
       block of Python (`import uuid ... print(default_api.create_drift_pattern
       (...))`) instead of a structured function call, and the whole script then
       surfaces as the error text — a wall of code in a red box.

    2. gemini-2.5-pro 429 (the 2026-06-09 memory_synthesizer case): a deployed
       pro sub-agent that hits Vertex Dynamic-Shared-Quota INSIDE its second LLM
       call emits no final output, so the supervisor's schema gate reports the
       SECONDARY symptom ("'action' is a required property") and never sees the
       429. When a pro node fails with that symptom shape, we CONFIRM against the
       node's own engine logs and, if a 429 is found, say so explicitly — this is
       a common shared-quota event, not a system bug. The memory write is
       non-blocking (the drift_event + emails already succeeded), so the run
       stays honest: node still goes red, but the message names the real cause.
    """
    low = raw.lower()
    looks_malformed = (
        "malformed function call" in low
        or "malformed_function_call" in low
        or ("print(default_api" in low)
        or ("import uuid" in low and "create_drift_pattern" in low)
    )
    if looks_malformed:
        label = "memory write" if node == "memory_synthesizer" else node
        return (
            f"{label} skipped (non-blocking): the model emitted a malformed "
            "tool call this run. The drift event and alerts already succeeded; "
            "the pattern store can be updated by a later event."
        )

    # gemini-2.5-pro shared-quota 429, surfaced only as a downstream symptom.
    engine_id = PRO_SUB_AGENT_ENGINES.get(node)
    if engine_id and _looks_like_pro_quota_symptom(raw):
        n429 = await asyncio.to_thread(_count_recent_429, engine_id, 180)
        if n429 > 0:
            label = "memory write" if node == "memory_synthesizer" else node
            return (
                f"{label} hit Vertex Dynamic Shared Quota (429 RESOURCE_EXHAUSTED) "
                f"— CONFIRMED in {node} engine logs. This is a common shared-quota "
                "event under regional peak load, not a system bug. The drift event "
                "and alerts already succeeded; the pattern store can be updated by "
                "a later event."
            )

    # Trim absurdly long raw errors so the card stays readable.
    return raw if len(raw) <= 280 else raw[:277] + "…"


def _summarize_node(author: str, chunk: dict) -> dict[str, Any] | None:
    """Translate one raw supervisor chunk into a node-update payload, or None if
    the chunk has nothing demo-worthy (e.g. an empty keepalive).

    Returns {kind, action?, summary?, output?} where kind is one of
    "active" (a tool call), "output" (a tool result), "done" (final text).
    """
    for p in _parts(chunk):
        fc = p.get("function_call")
        if fc:
            return {"kind": "active", "action": fc.get("name", "tool")}
        fr = p.get("function_response")
        if fr:
            return {"kind": "output", "summary": f"{fr.get('name', 'tool')} returned"}

    # No tool part — look for a final text/JSON output.
    parsed = _first_json_part(chunk)
    if parsed is not None:
        return {"kind": "done", "summary": _node_summary(author, parsed), "output": parsed}

    # Plain non-JSON text (rare); surface a short trace line.
    text = "".join(p.get("text", "") for p in _parts(chunk) if p.get("text"))
    if text.strip():
        return {"kind": "output", "summary": text.strip()[:120]}
    return None


def _node_summary(author: str, parsed: dict) -> str:
    """A one-line human summary of an agent's final §3.x.2 output for the UI."""
    if author == "claim_extractor":
        n = len(parsed.get("claims", []) or [])
        return f"extracted {n} claim(s)"
    if author == "drift_analyzer":
        ms = parsed.get("materiality_score")
        ds = parsed.get("drift_summary") or ""
        head = (ds[:90] + "…") if len(ds) > 90 else ds
        return f"materiality {ms} — {head}" if ms is not None else head or "drift analyzed"
    if author == "citation_finder":
        n = parsed.get("total_found")
        if n is None:
            n = len(parsed.get("affected_citations", []) or [])
        return f"found {n} affected citation(s)"
    if author == "notifier":
        subj = parsed.get("subject") or ""
        return f"drafted: {subj[:90]}" if subj else "notification drafted"
    if author == "memory_synthesizer":
        return f"memory {parsed.get('action', 'written')}"
    return "done"


# --- precise-id capture from the stream -------------------------------------

def _extract_drift_event_id(chunk: dict) -> str | None:
    """The supervisor re-emits the minted drift_event as a supervisor_agent
    event whose text part is the full §3.2.2 JSON (agent.py:327-334)."""
    if chunk.get("author") != "supervisor_agent":
        return None
    parsed = _first_json_part(chunk)
    if parsed and parsed.get("event_id"):
        return str(parsed["event_id"])
    return None


def _extract_pattern_write(chunk: dict) -> dict | None:
    """If this chunk carries the create/update_drift_pattern tool round-trip,
    return {pattern_id, result, source_event_id?} captured from BOTH the
    function_call args and the function_response output (cross-checked).

    Real shapes (apps/dispatcher/_stub_stream.json ev 10/11):
      call.args   = {pattern_id, source_event_id, now_iso}
      response    = {content:[{text: '{"results":[{"data":{"execution":
                     {"output":{"_id":..., "result":"updated"}}}}]}'}], isError}
    """
    out: dict[str, Any] = {}
    for p in _parts(chunk):
        fc = p.get("function_call")
        if fc and fc.get("name") in ("create_drift_pattern", "update_drift_pattern"):
            args = fc.get("args") or {}
            if args.get("pattern_id"):
                out.setdefault("pattern_id", str(args["pattern_id"]))
            if args.get("source_event_id"):
                out["source_event_id"] = str(args["source_event_id"])
            # create => result is "created"; refined below by the response.
            out.setdefault("result", "created" if fc["name"] == "create_drift_pattern" else "updated")

        fr = p.get("function_response")
        if fr and fr.get("name") in ("create_drift_pattern", "update_drift_pattern"):
            resp = fr.get("response") or {}
            content = resp.get("content")
            if isinstance(content, list) and content and content[0].get("text"):
                try:
                    inner = json.loads(content[0]["text"])
                    eo = inner["results"][0]["data"]["execution"]["output"]
                    if eo.get("_id"):
                        out["pattern_id"] = str(eo["_id"])  # authoritative
                    if eo.get("result"):
                        out["result"] = str(eo["result"])
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    pass
    return out or None


# --- teardown (precise id only) ---------------------------------------------

def _teardown(
    client: ElasticsearchHttpClient,
    event_id: str | None,
    pattern_write: dict | None,
) -> dict[str, Any]:
    """Undo EXACTLY this run's writes by captured id. Never batch-deletes.

    Returns a report dict for the teardown(post) SSE frame.
    """
    report: dict[str, Any] = {"event_deleted": False, "pattern_action": "none", "warnings": []}

    # 1) the minted drift_event.
    # NOTE: when this playground drives the supervisor DIRECTLY (bypassing the
    # dispatcher), NOTHING persists the drift_event to ES — the supervisor only
    # mints+echoes the id in its event stream; the dispatcher is what writes the
    # drift_events row in production. So a 404 here is EXPECTED and CLEAN (there
    # is simply no residue to remove), not a failure. _delete_idempotent treats
    # 404 as a no-op so we don't cry wolf.
    if event_id:
        deleted, existed = _delete_idempotent(
            client, f"/{DRIFT_EVENTS_INDEX}/_doc/{event_id}?refresh=wait_for"
        )
        if deleted is None:  # a real (non-404) error
            report["warnings"].append(f"drift_event {event_id} delete failed: {existed}")
        else:
            report["event_deleted"] = existed  # True if a row actually existed
    else:
        report["warnings"].append("no drift_event id captured; nothing deleted")

    # 2) the pattern memory_synthesizer wrote
    if pattern_write and pattern_write.get("pattern_id"):
        pid = pattern_write["pattern_id"]
        result = pattern_write.get("result")
        # The event_id appended to the pattern: prefer the one the supervisor
        # minted (== drift_event id); fall back to the call's source_event_id.
        appended = event_id or pattern_write.get("source_event_id")
        try:
            if result == "created":
                deleted, _ = _delete_idempotent(
                    client, f"/{DRIFT_PATTERNS_INDEX}/_doc/{pid}?refresh=wait_for"
                )
                if deleted is None:
                    raise RuntimeError("delete returned a non-404 error")
                report["pattern_action"] = "deleted"
            else:  # "updated" (or unknown -> treat as update, the safe inverse)
                if not appended:
                    report["warnings"].append(
                        f"pattern {pid} was updated but no appended event_id captured; "
                        f"left for manual cleanup"
                    )
                else:
                    _revert_pattern_append(client, pid, appended)
                    report["pattern_action"] = "reverted"
        except Exception as exc:  # noqa: BLE001
            log.warning("teardown: pattern %s undo failed: %s", pid, exc)
            report["warnings"].append(f"pattern {pid} undo failed: {exc}")
    elif pattern_write is not None:
        report["warnings"].append("pattern write detected but no pattern_id captured")

    return report


def _delete_idempotent(client: ElasticsearchHttpClient, path: str) -> tuple[bool | None, Any]:
    """DELETE a doc, treating a 404 as a clean no-op (the doc was never there).

    Returns (deleted, existed_or_error):
      (True, True)   -> a doc existed and was deleted
      (True, False)  -> nothing to delete (404) — clean, NOT a warning
      (None, <err>)  -> a real (non-404) error; <err> is the exception
    """
    try:
        client.request("DELETE", path)
        return True, True
    except Exception as exc:  # noqa: BLE001 — teardown must not raise
        if "HTTP 404" in str(exc):
            return True, False
        log.warning("teardown: delete %s failed: %s", path, exc)
        return None, exc


def _revert_pattern_append(client: ElasticsearchHttpClient, pattern_id: str, event_id: str) -> None:
    """Remove exactly `event_id` from a pattern's source_event_ids and recompute
    support_count. The update workflow is append-only set-union, so this is its
    exact inverse and leaves any concurrent production append (a different
    event_id) untouched. No-op if event_id isn't present."""
    body = {
        "script": {
            "lang": "painless",
            "source": (
                "if (ctx._source.source_event_ids != null) {"
                "  ctx._source.source_event_ids.removeIf(id -> id == params.eid);"
                "  ctx._source.support_count = ctx._source.source_event_ids.size();"
                "}"
            ),
            "params": {"eid": event_id},
        }
    }
    client.request("POST", f"/{DRIFT_PATTERNS_INDEX}/_update/{pattern_id}?refresh=wait_for", body)


# --- Gmail send (copied from dispatcher; see module docstring on why) --------

_gmail_service: Any | None = None


def _get_gmail_service() -> Any:
    """Lazily load OAuth secrets from Secret Manager and build a Gmail v1 service.
    Mirrors apps/dispatcher/main.py:642-667."""
    global _gmail_service
    if _gmail_service is not None:
        return _gmail_service

    from google.cloud import secretmanager
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    sm = secretmanager.SecretManagerServiceClient()

    def fetch(secret_name: str) -> str:
        path = f"projects/{project}/secrets/{secret_name}/versions/latest"
        return sm.access_secret_version(name=path).payload.data.decode("utf-8")

    creds = Credentials(
        token=None,
        refresh_token=fetch(GMAIL_SECRETS["refresh_token"]),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=fetch(GMAIL_SECRETS["client_id"]),
        client_secret=fetch(GMAIL_SECRETS["client_secret"]),
    )
    _gmail_service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    log.info("Gmail service initialized (OAuth secrets loaded)")
    return _gmail_service


def _send_sync(to: str, subject: str, body: str) -> str:
    """Blocking Gmail send. Returns the Gmail message id.
    Mirrors apps/dispatcher/main.py:670-683.

    The cached Gmail service wraps a single keep-alive httplib2 socket. When we
    send several alerts back-to-back, Gmail's server can close that socket
    between requests; reusing the dead connection then raises BrokenPipeError /
    ConnectionError mid-send (every other alert showed `[Errno 32] Broken pipe`
    and silently failed to deliver). On a connection-level error we drop the
    cached service so the next attempt builds a fresh socket, and retry once."""
    global _gmail_service
    msg = MIMEText(body)
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = _get_gmail_service().users().messages().send(
                userId="me", body={"raw": raw},
            ).execute()
            return resp.get("id", "")
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            # Stale/closed socket — discard the cached service so the retry
            # rebuilds a fresh connection, then try once more.
            last_exc = exc
            log.warning("Gmail send hit a connection error (attempt %d); "
                        "rebuilding service and retrying: %s", attempt + 1, exc)
            _gmail_service = None
    raise last_exc  # type: ignore[misc]


# --- notifier output extraction (mirrors dispatcher.extract_notifier_outputs) -

def _extract_notifier_outputs(events: list[dict]) -> list[dict]:
    """One §3.4.2 envelope per notifier invocation (grouped by invocation_id).
    Empty when N=0 (the HCQ stub case — no OpenAlex citing works)."""
    by_invocation: dict[str, list[dict]] = {}
    for ev in events:
        if ev.get("author") != "notifier":
            continue
        by_invocation.setdefault(ev.get("invocation_id") or "", []).append(ev)

    out: list[dict] = []
    for group in by_invocation.values():
        for chunk in reversed(group):
            parsed = _first_json_part(chunk)
            if parsed and (parsed.get("subject") or parsed.get("body")):
                out.append(parsed)
                break
    return out


def _fallback_alert(drift_summary: str | None) -> dict[str, str]:
    """When N=0 (no citing works) build one drift-alert email so the judge always
    receives mail demonstrating the notifier path."""
    summary = drift_summary or "A claim drift was detected between the preprint and published versions."
    return {
        "subject": "[ClaimDrift] Drift detected in a paper you may cite",
        "body": (
            "ClaimDrift detected a material claim drift between a preprint and "
            "its published version:\n\n"
            f"{summary}\n\n"
            "This is a live demonstration of the ClaimDrift 5-agent pipeline. "
            "In production this alert is sent to authors who cited the affected claim."
        ),
    }


# --- engine handle (cached + warmable) --------------------------------------

_engine: Any = None


def get_engine() -> Any:
    """init Vertex and resolve the supervisor engine — once, cached.

    vertexai itself is imported at module top (see the note there); this only
    does the cheap-ish init + the engine.get() network round-trip. SYNCHRONOUS,
    so async callers wrap it in asyncio.to_thread. Cached at module level so the
    startup warmup (server.py) and the request path share one hot handle — with
    startup pre-warming the first real request pays no init cost."""
    global _engine
    if _engine is None:
        vertexai.init(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        _engine = agent_engines.get(SUPERVISOR_ENGINE_ID)
        log.info("supervisor engine warmed (id=%s)", SUPERVISOR_ENGINE_ID)
    return _engine


# --- the live orchestration stream ------------------------------------------

async def orchestrate(email: str) -> AsyncIterator[str]:
    """Run the full supervisor pipeline once, light up nodes live, send the
    judge their drift-alert email, then tear down this run's writes precisely."""
    # Emit run.started as the VERY FIRST thing — before any import or client
    # construction. Cloud Run does not flush the response headers (or any body)
    # until the ASGI app yields its first chunk, so anything slow that runs
    # BEFORE the first yield delays the entire stream's first byte. `import
    # vertexai` is a heavy import (~10-20s cold on Cloud Run); having it above
    # the first yield is exactly what made experiment 2's pipeline sit blank for
    # tens of seconds while experiment 1 (which imports vertexai only AFTER its
    # first yield) streamed fine. run.started depends on nothing runtime, so it
    # goes out immediately; the slow setup happens after the UI already lit up.
    yield _sse("run.started", {
        "case": {
            "preprint_doi": ENVELOPE["preprint"]["doi"],
            "published_doi": ENVELOPE["published"]["doi"],
            "title": ENVELOPE["preprint"]["title"],
        },
        "pipeline": PIPELINE,
        "judge_email": email,
    })
    yield _sse("teardown", {"phase": "pre", "note": "precise-id teardown armed (no batch delete)"})

    client = ElasticsearchHttpClient()

    # Visible feedback BEFORE the (possibly slow) engine warm-up. On a cold
    # autoscaled instance get_engine() pays ~15s for `import vertexai` + the
    # first agent_engines.get() round-trip. Experiment 1 (A/B) never *looked*
    # stuck through the same import because it happens mid-run while a card shows
    # "analyzing…"; here we do the same — light the pipeline as "starting" so the
    # judge sees motion instead of a frozen board. With --min-instances=1 the
    # resident instance is already warm (sub-second), so this only matters for a
    # concurrently-autoscaled instance.
    yield _sse("pipeline.warming", {"detail": "starting supervisor engine…"})

    engine = await asyncio.to_thread(get_engine)
    yield _sse("pipeline.ready", {"detail": "engine ready — running pipeline"})
    message = json.dumps(ENVELOPE, ensure_ascii=False)

    all_events: list[dict] = []
    started_lanes: dict[str, dict[str, int]] = {}   # author -> {invocation_id: lane}
    seen_authors: set[str] = set()
    captured_event_id: str | None = None
    captured_pattern: dict | None = None
    drift_summary: str | None = None

    def _lane(author: str, chunk: dict) -> int | None:
        """Assign a stable lane index for fan-out authors (claim_extractor ×2,
        notifier ×N) keyed by invocation_id, in arrival order."""
        if author not in ("claim_extractor", "notifier"):
            return None
        inv = chunk.get("invocation_id") or ""
        lanes = started_lanes.setdefault(author, {})
        if inv not in lanes:
            lanes[inv] = len(lanes)
        return lanes[inv]

    # --- retry wrapper around the whole supervisor stream ------------------
    # drift_analyzer (gemini-2.5-pro) runs on Vertex Dynamic Shared Quota; under
    # regional peak load the model call gets a 429 the Agent Engine swallows, so
    # drift_analyzer yields nothing and the supervisor's core-chain RuntimeError
    # is swallowed too — the stream just ENDS after claim_extractor. The
    # supervisor's own hardening retry (max_attempts=3, ~1-2s backoff) burns out
    # in seconds, far too fast for a minute-scale shared-quota recovery. So we
    # retry the WHOLE supervisor run here with a minute-scale backoff, surfacing
    # `pipeline.retrying` so the judge sees motion instead of a dead board.
    # Failure signature: drift_analyzer minted no drift_event AND produced no
    # §3.2.2 drift_summary. Root-caused / hardened 2026-06-08.
    MAX_ATTEMPTS = 3          # 1 initial + 2 retries
    RETRY_BACKOFF_S = 15      # minute-scale waits for shared-quota recovery
    # Transient Agent-Engine stream failures: the gRPC stream is killed mid-run
    # (server-side RST_STREAM → InternalServerError "Stream removed", or a 503
    # ServiceUnavailable / RPC abort). These are NOT our logic errors and NOT a
    # 429 — they surface as an exception OUT of `async_stream_query`, so without
    # catching them the whole run dies after claim_extractor with only a
    # teardown.warning. Treat them like an empty attempt: retry with backoff.
    from google.api_core import exceptions as _gapi_exc  # local: keep top light
    _TRANSIENT_STREAM_ERRORS = (
        _gapi_exc.InternalServerError,   # 500, incl. RST_STREAM "Stream removed"
        _gapi_exc.ServiceUnavailable,    # 503
        _gapi_exc.Aborted,               # RPC aborted mid-stream
        _gapi_exc.DeadlineExceeded,      # stream stalled past deadline
    )
    email_skipped = False
    try:
        for attempt in range(MAX_ATTEMPTS):
            # reset per-attempt state (a retry is a fresh supervisor run). Use
            # .clear() not rebinding so the _lane() closure keeps the same dict.
            all_events.clear()
            seen_authors.clear()
            started_lanes.clear()
            captured_event_id = None
            captured_pattern = None
            drift_summary = None
            stream_error: Exception | None = None

            try:
                async for chunk in engine.async_stream_query(message=message, user_id="playground::orchestration"):
                    all_events.append(chunk)
                    author = chunk.get("author") or ""

                    # capture write ids regardless of which node they ride on
                    ev_id = _extract_drift_event_id(chunk)
                    if ev_id:
                        captured_event_id = ev_id
                        parsed = _first_json_part(chunk)
                        if parsed:
                            drift_summary = parsed.get("drift_summary") or drift_summary
                        yield _sse("drift.minted", {"event_id": ev_id, "drift_summary": drift_summary})

                    pw = _extract_pattern_write(chunk)
                    if pw:
                        # merge across the call chunk and the response chunk
                        captured_pattern = {**(captured_pattern or {}), **pw}

                    # error events
                    if chunk.get("error_code") or chunk.get("error_message"):
                        node = author if author in _NODE_IDS else "supervisor"
                        raw_msg = str(chunk.get("error_message") or chunk.get("error_code"))
                        yield _sse("node.error", {
                            "node": node, "lane": _lane(author, chunk),
                            "message": await _friendly_node_error(node, raw_msg),
                        })
                        continue

                    if author not in _NODE_IDS:
                        continue  # supervisor_agent / unknown — already handled above

                    lane = _lane(author, chunk)
                    if author not in seen_authors or (lane is not None and lane > 0):
                        seen_authors.add(author)
                        yield _sse("node.started", {
                            "node": author, "lane": lane,
                            "label": next((p["label"] for p in PIPELINE if p["id"] == author), author),
                        })

                    upd = _summarize_node(author, chunk)
                    if upd is None:
                        continue
                    kind = upd.pop("kind")
                    if kind == "active":
                        yield _sse("node.active", {"node": author, "lane": lane, **upd})
                    elif kind == "output":
                        yield _sse("node.output", {"node": author, "lane": lane, **upd})
                    elif kind == "done":
                        if author == "drift_analyzer":
                            parsed = upd.get("output") or {}
                            drift_summary = parsed.get("drift_summary") or drift_summary
                        yield _sse("node.done", {"node": author, "lane": lane, **upd})

            except _TRANSIENT_STREAM_ERRORS as exc:
                # Agent-Engine stream killed mid-run (e.g. RST_STREAM →
                # "Stream removed"). Not a logic error and not a 429; the
                # run otherwise dies after claim_extractor with only a
                # teardown.warning. Record it and fall through to the same
                # retry/backoff path used for an empty (suspected-429) attempt.
                stream_error = exc
                log.warning("supervisor stream interrupted on attempt %d: %s",
                            attempt + 1, exc)

            # --- did drift_analyzer produce anything this attempt? --------
            if captured_event_id is not None or drift_summary is not None:
                break  # success — fall through to email

            # empty output → confirm a 429 in EITHER sub-agent's logs and
            # attribute it to the right node. claim_extractor runs first, so if
            # it 429s the chain stalls before drift_analyzer is ever reached;
            # checking only drift_analyzer (the old bug) missed that case.
            hits = await asyncio.to_thread(_which_recent_429, 180)
            confirmed = bool(hits)
            # the node to blame on the board: the earliest-running engine that
            # actually 429'd (claim_extractor before drift_analyzer); fall back
            # to drift_analyzer when we couldn't confirm which.
            culprit = "claim_extractor" if "claim_extractor" in hits else "drift_analyzer"
            who = " + ".join(hits) if hits else "drift_analyzer"

            if attempt < MAX_ATTEMPTS - 1:
                # retry: tell the judge we're retrying, reset the board, back off.
                # Two failure modes share this path: (a) the stream was killed
                # mid-run (stream_error set — RST_STREAM/503/abort), (b) the stream
                # ended cleanly but drift_analyzer produced nothing (suspected 429).
                if stream_error is not None:
                    retry_node = "supervisor"
                    retry_detail = (
                        f"The supervisor stream was interrupted ({type(stream_error).__name__}: "
                        f"{str(stream_error)[:120]}) — a transient Agent-Engine error, not a "
                        f"pipeline fault. Retrying (attempt {attempt + 2}/{MAX_ATTEMPTS}) "
                        f"after a {RETRY_BACKOFF_S}s backoff…"
                    )
                else:
                    retry_node = culprit
                    retry_detail = (
                        f"{who} (gemini-2.5-pro) returned no drift report — "
                        + ("429 RESOURCE_EXHAUSTED confirmed in engine logs"
                           if confirmed else "suspected Vertex shared-quota 429")
                        + f". Retrying the pipeline (attempt {attempt + 2}/{MAX_ATTEMPTS}) "
                        f"after a {RETRY_BACKOFF_S}s backoff for shared-quota recovery…"
                    )
                yield _sse("pipeline.retrying", {
                    "node": retry_node,
                    "attempt": attempt + 1,
                    "max_attempts": MAX_ATTEMPTS,
                    "confirmed": confirmed,
                    "backoff_s": RETRY_BACKOFF_S,
                    "detail": retry_detail,
                })
                yield _sse("pipeline.reset", {"detail": "clearing pipeline for retry"})
                await asyncio.sleep(RETRY_BACKOFF_S)
                continue

            # retries exhausted → surface the failure, and DO NOT send a fallback
            # email. Distinguish a persistent stream interruption from a 429 so the
            # judge sees the true cause (both reuse the quota_error banner).
            email_skipped = True
            if stream_error is not None:
                yield _sse("pipeline.quota_error", {
                    "node": "supervisor",
                    "confirmed": False,
                    "attempts": MAX_ATTEMPTS,
                    "detail": (
                        f"The supervisor stream kept getting interrupted "
                        f"({type(stream_error).__name__}: {str(stream_error)[:120]}) — a "
                        f"transient Agent-Engine error on Vertex. Retried {MAX_ATTEMPTS - 1}× "
                        "and the stream was cut each time, so the pipeline could not finish "
                        "and no alert email was sent. This usually clears on its own; please "
                        "retry shortly."
                    ),
                })
            else:
                yield _sse("pipeline.quota_error", {
                    "node": culprit,
                    "confirmed": confirmed,
                    "attempts": MAX_ATTEMPTS,
                    "detail": (
                        f"{who} (gemini-2.5-pro) hit Vertex Dynamic Shared "
                        "Quota (429 RESOURCE_EXHAUSTED) — "
                        + ("CONFIRMED in engine logs" if confirmed
                           else "suspected (empty model stream)")
                        + f". Retried {MAX_ATTEMPTS - 1}× with backoff and still no "
                        "capacity. The pipeline cannot continue without the drift "
                        "report, so no alert email was sent. Capacity is shared "
                        "region-wide and fluctuates per minute; please retry shortly."
                    ),
                })

        # --- email: send the judge their drift alert ---------------------
        # Only when drift_analyzer actually produced a report. On quota failure
        # (email_skipped) a "drift detected" email would be misleading, so we
        # skip straight to teardown.
        emails_sent = 0
        if not email_skipped:
            notifications = _extract_notifier_outputs(all_events)
            drafts = notifications or [_fallback_alert(drift_summary)]
            for draft in drafts:
                subject = draft.get("subject") or "[ClaimDrift] Drift alert"
                body = draft.get("body") or ""
                yield _sse("email.sending", {"to": email, "subject": subject})
                try:
                    msg_id = await asyncio.to_thread(_send_sync, email, subject, body)
                    emails_sent += 1
                    yield _sse("email.sent", {"to": email, "subject": subject, "message_id": msg_id})
                except Exception as exc:  # noqa: BLE001
                    log.exception("playground email send failed")
                    yield _sse("email.failed", {"to": email, "subject": subject, "error": str(exc)})

    finally:
        # precise-id teardown ALWAYS runs (success, error, or abort)
        report = await asyncio.to_thread(_teardown, client, captured_event_id, captured_pattern)
        for w in report.get("warnings", []):
            yield _sse("teardown.warning", {"note": w})
        yield _sse("teardown", {
            "phase": "post",
            "event_deleted": report["event_deleted"],
            "pattern_action": report["pattern_action"],
        })

    yield _sse("run.complete", {
        "event_id": captured_event_id,
        "pattern_action": report["pattern_action"],
        "emails_sent": emails_sent,
        "summary": drift_summary,
    })
