"""E4 (layer 2) — end-to-end drift_analyzer proof that de-hardcoded retrieval
generalizes to a domain OUTSIDE the demo fixture.

Layer 1 (e4_generalization_probe.py) proved the RETRIEVAL layer in isolation.
Layer 2 proves the WHOLE agent: that drift_analyzer, run on an economics
effect-size-reduction case (not the clinical outcome-switch fixture), builds an
economics-flavored structured query (NOT the old AI-diagnostic hint), retrieves
the economics pattern, and does NOT misuse the clinical outcome-switch memory.

This script is SETUP only (the LLM run is the manual step, per the agreed
workflow):
  - `--setup`   : inject a persistent economics effect_size_reduction pattern
                  (record_source tag) so the agent has a correct off-fixture
                  memory to find, and write the prompt file.
  - `--teardown`: delete the injected pattern.

Run from agents/:
    uv run python scripts/e4_e2e_setup.py --setup
    # ... paste the prompt into drift_analyzer, save output as e4_treatment.json ...
    uv run python scripts/e4_e2e_setup.py --teardown
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

PROBE_TAG = "e4_e2e_probe"
PATTERN_ID = "e4-e2e-econ-effectsize-0001"
RUN_DIR = _AGENTS_ROOT / "evals" / "results" / "memory-loop-e4-2026-05-31"

ECON_PATTERN = {
    "pattern_id": PATTERN_ID,
    "pattern_description": (
        "In empirical economics, headline causal-effect magnitudes reported in working "
        "papers are frequently revised downward in the published version after "
        "referee-requested robustness checks, and the main coefficient sometimes loses "
        "statistical significance. This effect-size reduction is a recurring publication "
        "drift in observational and quasi-experimental economics studies."
    ),
    "pattern_type": "effect_size_reduction",
    "domain_tags": ["economics", "causal-inference", "robustness", "effect-size-reduction"],
    "record_source": PROBE_TAG,
    "source_event_ids": [],
    "support_count": 18,
    "created_at": "2026-05-31T00:00:00Z",
    "last_updated_at": "2026-05-31T00:00:00Z",
}

# An economics effect-size-reduction case — different DOMAIN and different DRIFT
# TYPE from the clinical outcome-switch fixture, so it exercises generalization.
CASE_INPUT = {
    "preprint_doi": "10.1101/eval.e4.econ.0001",
    "preprint_version_compared": "v2",
    "published_doi": "10.1000/eval.e4.econ.0001",
    "preprint_claims": [
        {
            "claim_id": "10.1101/eval.e4.econ.0001::v2::abstract::0",
            "text": ("A minimum-wage increase of 10% reduced teen employment by 6.2% "
                     "(p<0.01) in our two-way fixed-effects specification across 48 states."),
            "claim_type": "quantitative",
            "hedging_level": "none",
        },
        {
            "claim_id": "10.1101/eval.e4.econ.0001::v2::abstract::1",
            "text": "These results provide robust causal evidence of large disemployment effects.",
            "claim_type": "comparative",
            "hedging_level": "none",
        },
    ],
    "published_claims": [
        {
            "claim_id": "10.1000/eval.e4.econ.0001::published::abstract::0",
            "text": ("After adding county-level controls and a border-discontinuity "
                     "robustness check, a 10% minimum-wage increase is associated with a "
                     "1.1% reduction in teen employment, not statistically significant "
                     "at conventional levels (p=0.21)."),
            "claim_type": "quantitative",
            "hedging_level": "moderate",
        },
        {
            "claim_id": "10.1000/eval.e4.econ.0001::published::abstract::1",
            "text": "The evidence for a disemployment effect is weaker than initially reported.",
            "claim_type": "comparative",
            "hedging_level": "moderate",
        },
    ],
}

INSTRUCTIONS = [
    "Use memory retrieval normally.",
    "When calling search_drift_patterns, build query_text from preprint claims, "
    "published claims, and a STRUCTURED drift descriptor inferred from THIS case "
    "(domain + study type + drift type + magnitude/direction). Do NOT use any "
    "hard-coded AI-diagnostic hint.",
    "This is an economics effect-size-reduction case, NOT a clinical outcome switch.",
    "Perform a relevance audit: only list a pattern in retrieved_patterns_used if its "
    "domain, drift type, and phenomenon genuinely match THIS economics case.",
    "Do NOT use the clinical primary-outcome-switch memory for this economics case.",
    "Return severity_calibration; if the economics effect_size_reduction memory is "
    "relevant, use its support_count to calibrate. If no memory is relevant, set "
    "memory_pattern_ids and evidence to [] and calibration_delta to 0.0.",
    "Do not put materiality_score inside individual claim_diffs.",
    "Return JSON only.",
]


def _prompt() -> str:
    lines = ["Run Drift Analyzer on the following case.", "", "Important:"]
    lines += [f"- {i}" for i in INSTRUCTIONS]
    lines += ["", "Input:", json.dumps(CASE_INPUT, indent=2), ""]
    return "\n".join(lines)


def _cleanup(client) -> int:
    body = {"query": {"term": {"record_source": PROBE_TAG}}}
    r = client.request("POST", f"/{DRIFT_PATTERNS_INDEX}/_delete_by_query?refresh=true", body)
    return int(r.get("deleted", 0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--teardown", action="store_true")
    args = ap.parse_args()
    if not (args.setup or args.teardown):
        ap.error("choose --setup or --teardown")

    client = ElasticsearchHttpClient()

    if args.teardown:
        n = _cleanup(client)
        print(f"teardown: deleted {n} E4 e2e probe pattern(s).")
        return 0

    # setup
    _cleanup(client)  # idempotent
    client.request("PUT", f"/{DRIFT_PATTERNS_INDEX}/_doc/{PATTERN_ID}?op_type=create&refresh=wait_for", ECON_PATTERN)
    print(f"injected economics effect_size_reduction pattern '{PATTERN_ID}' "
          f"(support_count={ECON_PATTERN['support_count']}, record_source={PROBE_TAG}).")

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "e4_treatment_prompt.md").write_text(_prompt())
    (RUN_DIR / "e4_case_input.json").write_text(json.dumps(CASE_INPUT, indent=2) + "\n")
    print(f"wrote prompt: {RUN_DIR / 'e4_treatment_prompt.md'}")
    print("\nNext (manual):")
    print(f"  1. paste {RUN_DIR / 'e4_treatment_prompt.md'} into drift_analyzer")
    print(f"  2. save its JSON output as {RUN_DIR / 'e4_treatment.json'}")
    print("  3. uv run python scripts/e4_e2e_check.py   (scores the output)")
    print("  4. uv run python scripts/e4_e2e_setup.py --teardown")
    print(f"\nExpected pass: retrieved_patterns_used includes '{PATTERN_ID}', NOT the "
          "clinical outcome-switch pattern; query is economics-flavored, no AI hint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
