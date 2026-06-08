"""E2 — memory-loop accumulation curve (the demo's core shot).

Proves the read-side payoff of the memory loop: as a drift pattern accumulates
more supporting evidence (`support_count`), the Drift Analyzer's
`calibrated_materiality` for the SAME case converges upward. We plot
`support_count -> calibrated_materiality`.

Why this is a CONTROLLED experiment, not a natural accumulation
----------------------------------------------------------------
The memory loop accumulates monotonically and is irreversible: once support
climbs to 50 you cannot rewind the cluster to support=5 to re-record. And a real
audit (391 drift_events / 0 outcome_switch patterns) never self-accumulates a
primary-outcome-switch pattern at demo scale. So each tier is built
INDEPENDENTLY, recorded, then torn down — reproducible and re-recordable.

The cases are REAL. Each `source_event_id` points at a genuine, registered
(NCT / ISRCTN / ACTRN), DOI-bearing outcome-switch case audited by the Oxford
CEBM COMPARE project (compare-trials.org). What we *control* is how many of
those real cases the agent's memory has accumulated (the `support_count` tier) —
like putting a thermometer into 0/50/100 C water and reading it, rather than
waiting for room temperature to drift. Honest framing for the demo:
"these are COMPARE-audited REAL outcome-switch cases; we control the historical
sample size the agent can see (5/20/35/50) and watch how severity calibration
converges with the base rate."

The §3.5.1 invariant `support_count == len(source_event_ids)` is respected at
every tier: tier N injects N real drift_events and one pattern whose
`source_event_ids` lists exactly those N event ids.

The dependent variable is held FIXED: every tier runs the same REAL, deliberately
ambiguous case (the tasimelteon SET trial, NCT01163032, whose pre-specified
co-primary clinical-response endpoint was demoted to a step-down endpoint in the
Lancet publication — see CASE_INPUT), so the ONLY thing that moves between tiers
is the pattern's `support_count`.

Everything written here is tagged `record_source=PROBE_TAG` and removed by
`--teardown`. This is a controlled experimental probe, not production memory:
the thing that must be torn down is the pattern whose support_count we set by
hand (leaving it would be a duplicate of the production outcome_switch pattern
and corrupt the base rate the §3.6 curator exists to protect). The real cases
themselves are harmless; the staged support is the摆拍 that must leave.

Workflow (per the agreed manual-LLM convention, same as E4):
    cd agents/
    # tier 5:
    uv run python scripts/e2_accumulation_curve.py --setup 5
    #   ... paste the printed prompt (now JUST the case JSON — same payload the
    #       production supervisor sends) into the PRODUCTION drift_analyzer
    #       (uv run adk web / adk run), read calibrated_materiality from its
    #       JSON output ...
    uv run python scripts/e2_accumulation_curve.py --record 5 <calibrated_materiality>
    uv run python scripts/e2_accumulation_curve.py --teardown
    # repeat for 20, 35, 50
    uv run python scripts/e2_accumulation_curve.py --plot

NOTE (2026-06-08): the E2 method now lives in the production drift_analyzer
prompt, so the pasted message contains NO analysis instructions — only the case.
The previously recorded 0.75->0.82 curve was produced by the old per-probe
INSTRUCTIONS and MUST be re-captured against the production prompt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_AGENTS_ROOT, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_AGENTS_ROOT / ".env")

from _shared.elastic_retrieval import DRIFT_PATTERNS_INDEX  # noqa: E402
from ingestion.common.elastic import ElasticsearchHttpClient  # noqa: E402

PROBE_TAG = "e2_accum_probe"
PATTERN_ID = "e2-accum-outcome-switch-0001"
DRIFT_EVENTS_INDEX = "drift_events"
TIERS = [5, 20, 35, 50]

CASES_FILE = _AGENTS_ROOT / "evals" / "fixtures" / "compare_outcome_switch_cases.json"
RUN_DIR = _AGENTS_ROOT / "evals" / "results" / "memory-loop-e2-2026-05-31"
RESULTS_FILE = RUN_DIR / "accumulation_readings.json"

# The dependent-variable case, held FIXED across every tier — only the pattern's
# support_count changes between tiers. This is a REAL, deliberately AMBIGUOUS
# outcome-switch case from the COMPARE dataset: the tasimelteon SET trial for
# non-24-hour sleep-wake disorder (Lockley et al., Lancet 2015;386:1754-64;
# ClinicalTrials.gov NCT01163032). COMPARE flagged it as primary_switched=1.
#
# The real switch: NCT01163032 PRE-SPECIFIED TWO co-primary endpoints —
#   (1) proportion entrained by aMT6s rhythm (month 1), and
#   (2) proportion with a clinical response (entrainment + N24CRS >=3).
# The Lancet paper reports endpoint (1) as THE single primary endpoint and
# DEMOTES endpoint (2) to a subordinate "planned step-down primary endpoint".
# This is deliberately ambiguous: a step-down/hierarchical testing order is a
# legitimate pre-specified design, so the diff ALONE reads as a defensible
# methodological choice (~medium severity). Only the base rate — that
# co-primary -> step-down demotion recurs as a way to protect a borderline
# headline result (here entrainment was just 20% vs 3%, p=0.0171) — reveals it
# as the same primary-outcome-switch phenomenon. That is exactly the gap the
# memory loop is meant to close, and why severity should climb with support.
# Numbers, endpoint names, and p-values below are quoted from the registry and
# the published Table 2 / Outcomes section.
CASE_INPUT = {
    "preprint_doi": "registry:NCT01163032:prespecified",
    "preprint_version_compared": "registered_protocol",
    "published_doi": "10.1016/S0140-6736(15)60031-9",
    "preprint_claims": [
        {
            "claim_id": "registry:NCT01163032::prespecified::primary::0",
            "text": ("Pre-specified co-primary endpoint 1 (SET): the proportion of "
                     "patients entrained, assessed from urinary 6-sulphatoxymelatonin "
                     "(aMT6s) rhythm at month 1, in the intention-to-treat population."),
            "claim_type": "quantitative",
            "hedging_level": "none",
        },
        {
            "claim_id": "registry:NCT01163032::prespecified::primary::1",
            "text": ("Pre-specified co-primary endpoint 2 (SET): the proportion of "
                     "patients with a clinical response, defined as entrainment of aMT6s "
                     "plus a score of >=3 on the Non-24 Clinical Response Scale (N24CRS)."),
            "claim_type": "quantitative",
            "hedging_level": "none",
        },
    ],
    "published_claims": [
        {
            "claim_id": "10.1016/S0140-6736(15)60031-9::published::primary::0",
            "text": ("In SET, the primary endpoint was the proportion of entrained "
                     "patients (aMT6s, month 1): 8/40 (20%) on tasimelteon vs 1/38 (3%) "
                     "on placebo (difference 17%, 95% CI 3.2-31.6; p=0.0171)."),
            "claim_type": "quantitative",
            "hedging_level": "none",
        },
        {
            "claim_id": "10.1016/S0140-6736(15)60031-9::published::stepdown::0",
            "text": ("The planned STEP-DOWN primary endpoint assessed the proportion of "
                     "patients with a clinical response (entrainment plus N24CRS >=3): "
                     "9/38 (24%) vs 0/34 (p=0.0028); it is presented as a subordinate "
                     "step-down endpoint rather than as a co-primary outcome."),
            "claim_type": "quantitative",
            "hedging_level": "moderate",
        },
    ],
}

# E2 sends NO analysis instructions. As of 2026-06-08 the entire E2 method
# (read support_count from the retrieved pattern, treat it as a CONTINUOUS
# base-rate strength, scale severity monotonically with diminishing returns,
# judge merits first) lives in the PRODUCTION drift_analyzer INSTRUCTION
# (agents/drift_analyzer/agent.py). E2 therefore exercises the shipped agent
# with its default prompt — the curve is now a property of the product, not of
# a probe-only prompt. Two consequences:
#   1. The curve must be RE-CAPTURED against the new production prompt; the
#      historical 0.75->0.82 readings were produced by the old per-probe
#      INSTRUCTIONS and no longer describe default behavior.
#   2. "Did you write the curve into the prompt?" is now answerable with the
#      strongest possible no: the same prompt ships in production, and it names
#      no support_count value — the agent reads the count from ES.
# The only thing E2 still controls is the staged support_count tier (the
# injected pattern), exactly as before. See README "Blind design".
INSTRUCTIONS: list[str] = []


def _load_cases() -> list[dict]:
    if not CASES_FILE.exists():
        raise SystemExit(f"missing case set {CASES_FILE} — run E2-a extraction first.")
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if len(cases) < max(TIERS):
        raise SystemExit(
            f"case set has only {len(cases)} cases but the top tier needs "
            f"{max(TIERS)}; lower TIERS or extract more COMPARE cases.")
    return cases


def _event_id(n: int) -> str:
    """Deterministic, structurally-valid (UUID-shaped suffix avoided on purpose:
    these are probe ids, kept human-legible and tagged for teardown)."""
    return f"e2-accum-event-{n:03d}"


def _make_drift_event(case: dict, idx: int) -> dict:
    """Turn one real COMPARE case into a drift_event document (§3.2.2 shape).

    The real registry id, DOI, and audited outcome counts go into the human
    fields so the document is fully traceable; record_source tags it for
    teardown.
    """
    reg = case["registry_id"]
    unreported = case.get("prespecified_unreported") or 0
    added = case.get("novel_added") or 0
    pri_switched = case.get("primary_switched") or 0
    desc = (
        f"COMPARE-audited outcome switching in '{case['title']}' "
        f"({case['journal']}, registry {reg}): "
        f"{unreported} pre-specified outcome(s) unreported, "
        f"{added} non-pre-specified outcome(s) silently added"
        + (f", {pri_switched} primary outcome(s) switched" if pri_switched else "")
        + f". Report: {case['report_url']}; registry: {case['registry_url']}."
    )
    # Fields restricted to the drift_events strict mapping
    # (elastic/mappings/drift_events.json): NO severity_calibration here — that
    # field lives only on the drift_analyzer OUTPUT, not on the persisted
    # drift_events index.
    return {
        "event_id": _event_id(idx),
        "preprint_doi": f"compare:{reg}:registered",
        "preprint_version_compared": "registered_protocol",
        "published_doi": case["report_url"],
        "detected_at": "2026-05-31T00:00:00Z",
        "analyzed_at": "2026-05-31T00:00:00Z",
        "drift_summary": desc,
        "claim_diffs": [
            {
                "diff_type": "outcome_switch",
                "preprint_claim_id": None,
                "published_claim_id": None,
                "preprint_text": (
                    f"Registered protocol/registry {reg} pre-specified outcomes "
                    "(primary efficacy + secondary endpoints) per CONSORT."),
                "published_text": (
                    f"Published {case['journal']} report: {unreported} pre-specified "
                    f"outcome(s) unreported and {added} novel outcome(s) added without "
                    "declaration."),
                "change_description": desc,
            }
        ],
        "materiality_score": 0.85,
        "retrieved_patterns_used": [],
        "record_source": PROBE_TAG,
    }


def _make_pattern(event_ids: list[str]) -> dict:
    """One outcome_switch pattern whose support_count == len(source_event_ids)
    (the §3.5.1 invariant). The description is GENERIC per contracts §3.6.4 —
    no specific DOIs or trial names live here (that is what source_event_ids is
    for); the staged support_count is the controlled variable."""
    return {
        "pattern_id": PATTERN_ID,
        "pattern_description": (
            "In randomized clinical trials, a pre-specified primary efficacy endpoint "
            "is frequently demoted to an exploratory or secondary outcome (or silently "
            "replaced by a feasibility/safety/process outcome) between the registered "
            "protocol and the published report. This primary-outcome switching is a "
            "recurring, high-severity publication drift that inflates apparent success "
            "and is systematically under-declared."),
        "pattern_type": "outcome_switch",
        "domain_tags": ["clinical-trial", "rct", "outcome-switching", "primary-endpoint"],
        "source_event_ids": list(event_ids),
        "support_count": len(event_ids),
        "record_source": PROBE_TAG,
        "created_at": "2026-05-31T00:00:00Z",
        "last_updated_at": "2026-05-31T00:00:00Z",
    }


def _prompt(tier: int) -> str:
    """The agent's user message: the case payload and NOTHING ELSE.

    This is deliberately byte-for-byte the same kind of input the production
    supervisor hands drift_analyzer — `json.dumps(envelope)` with no surrounding
    prose (see agents/supervisor_agent/agent.py: `message = json.dumps(envelope)`).
    The agent therefore sees ONLY case data; the entire analysis method,
    including how to use a retrieved pattern's support_count, comes from the
    production drift_analyzer INSTRUCTION (its system prompt) — not from here.

    BLIND by design: this message reveals neither the tier nor the support_count.
    The agent must read support_count from the pattern it retrieves out of ES, so
    the accumulation curve can only come from the injected base rate, not from a
    number we fed it. (`tier` is used only for the output filename.)
    """
    return json.dumps(CASE_INPUT, indent=2) + "\n"


def _cleanup(client) -> tuple[int, int]:
    body = {"query": {"term": {"record_source": PROBE_TAG}}}
    rp = client.request("POST", f"/{DRIFT_PATTERNS_INDEX}/_delete_by_query?refresh=true", body)
    re = client.request("POST", f"/{DRIFT_EVENTS_INDEX}/_delete_by_query?refresh=true", body)
    return int(rp.get("deleted", 0)), int(re.get("deleted", 0))


def _baseline(client) -> int:
    """No-memory baseline: ensure NO probe pattern exists, then write the SAME
    case prompt the tiers use. Pasted into drift_analyzer, the agent retrieves
    but finds no relevant pattern (cold start) and must judge the case on its
    own merits. This is the honest memory-free control for the 3-state demo
    (no-memory -> support5 -> support20), produced by the production prompt with
    the identical case payload — the only difference from a tier run is that ES
    has nothing to retrieve.
    """
    dp, de = _cleanup(client)
    print(f"baseline: cleared {dp} probe pattern(s) + {de} probe event(s); ES has "
          f"no e2_accum_probe pattern, so retrieval will come up empty.")
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    prompt_path = RUN_DIR / "e2_prompt_baseline.md"
    prompt_path.write_text(_prompt(0), encoding="utf-8")  # same CASE_INPUT, tier only names the file
    print(f"wrote prompt: {prompt_path}")
    print("\nNext (manual):")
    print(f"  1. paste {prompt_path} into the PRODUCTION drift_analyzer (uv run adk web)")
    print("  2. confirm retrieved_patterns_used == [] (no memory retrieved)")
    print("  3. read severity_calibration.baseline_materiality_without_memory and")
    print("     materiality_score — this is the no-memory control for the demo.")
    print("  (nothing to teardown; baseline injects nothing.)")
    return 0


def _setup(client, tier: int) -> int:
    if tier not in TIERS:
        print(f"warning: {tier} is not a standard tier {TIERS}; proceeding anyway.")
    cases = _load_cases()[:tier]
    _cleanup(client)  # idempotent: one tier live at a time

    event_ids = []
    for i, case in enumerate(cases):
        ev = _make_drift_event(case, i)
        event_ids.append(ev["event_id"])
        client.request(
            "PUT",
            f"/{DRIFT_EVENTS_INDEX}/_doc/{ev['event_id']}?op_type=create&refresh=false",
            ev,
        )
    pattern = _make_pattern(event_ids)
    client.request(
        "PUT",
        f"/{DRIFT_PATTERNS_INDEX}/_doc/{PATTERN_ID}?op_type=create&refresh=wait_for",
        pattern,
    )
    assert pattern["support_count"] == len(pattern["source_event_ids"]) == tier

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    prompt_path = RUN_DIR / f"e2_prompt_support{tier}.md"
    prompt_path.write_text(_prompt(tier), encoding="utf-8")

    print(f"injected {tier} real COMPARE drift_events + 1 outcome_switch pattern "
          f"'{PATTERN_ID}' (support_count={tier}, record_source={PROBE_TAG}).")
    print(f"§3.5.1 invariant OK: support_count == len(source_event_ids) == {tier}.")
    print(f"wrote prompt: {prompt_path}")
    print("\nNext (manual):")
    print(f"  1. paste {prompt_path} into drift_analyzer (uv run adk web / adk run)")
    print("  2. read severity_calibration.calibrated_materiality from its JSON output")
    print(f"  3. uv run python scripts/e2_accumulation_curve.py --record {tier} <value>")
    print("  4. uv run python scripts/e2_accumulation_curve.py --teardown")
    print(f"     then --setup {TIERS[(TIERS.index(tier)+1) % len(TIERS)]} for the next tier"
          if tier in TIERS else "")
    return 0


def _record(tier: int, value: float) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    readings = {}
    if RESULTS_FILE.exists():
        readings = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    readings[str(tier)] = float(value)
    RESULTS_FILE.write_text(json.dumps(readings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"recorded support_count={tier} -> calibrated_materiality={value}")
    print(f"readings so far: { {k: readings[k] for k in sorted(readings, key=int)} }")
    missing = [t for t in TIERS if str(t) not in readings]
    print(f"remaining tiers: {missing}" if missing else "all tiers recorded — run --plot.")
    return 0


def _plot() -> int:
    if not RESULTS_FILE.exists():
        raise SystemExit(f"no readings yet at {RESULTS_FILE} — run --record first.")
    readings = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    pts = sorted(((int(k), float(v)) for k, v in readings.items()))
    if not pts:
        raise SystemExit("no readings to plot.")

    print("\nE2 — memory accumulation curve  (support_count -> calibrated_materiality)\n")
    width = 40
    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    span = (hi - lo) or 1.0
    for sc, val in pts:
        filled = int(round((val - lo) / span * (width - 1))) + 1 if hi > lo else width
        bar = "#" * filled + "." * (width - filled)
        print(f"  support={sc:>3}  |{bar}|  {val:.3f}")
    print(f"\n  y-axis: calibrated_materiality   range [{lo:.3f}, {hi:.3f}]")
    if pts[-1][1] >= pts[0][1]:
        print(f"  convergence: {pts[0][1]:.3f} (support={pts[0][0]}) -> "
              f"{pts[-1][1]:.3f} (support={pts[-1][0]}), "
              f"delta=+{pts[-1][1]-pts[0][1]:.3f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="E2 accumulation curve probe.")
    ap.add_argument("--setup", type=int, metavar="N", help="inject tier N (real cases + pattern, support_count=N)")
    ap.add_argument("--baseline", action="store_true",
                    help="no-memory control: clear probe pattern + write the same case prompt (retrieval comes up empty)")
    ap.add_argument("--record", nargs=2, metavar=("N", "MATERIALITY"),
                    help="record the calibrated_materiality read for tier N")
    ap.add_argument("--plot", action="store_true", help="print the ASCII convergence curve")
    ap.add_argument("--teardown", action="store_true", help="delete all e2_accum_probe docs")
    args = ap.parse_args()

    if not (args.setup or args.baseline or args.record or args.plot or args.teardown):
        ap.error("choose one of --setup N / --baseline / --record N V / --plot / --teardown")

    if args.plot:
        return _plot()
    if args.record:
        return _record(int(args.record[0]), float(args.record[1]))

    client = ElasticsearchHttpClient()
    if args.teardown:
        dp, de = _cleanup(client)
        print(f"teardown: deleted {dp} pattern(s) + {de} drift_event(s) tagged {PROBE_TAG}.")
        return 0
    if args.baseline:
        return _baseline(client)
    return _setup(client, args.setup)


if __name__ == "__main__":
    raise SystemExit(main())
