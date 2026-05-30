#!/usr/bin/env python3
"""Score a small ClaimDrift memory-loop A/B experiment.

This script is intentionally stdlib-only. It does not call the deployed agents.
Instead, it evaluates the JSON artifacts captured from agent runs so the demo
team can keep prompt iteration reproducible:

1. Run Drift Analyzer baseline with memory disabled or an empty drift_patterns
   index, save JSON.
2. Run Memory Synthesizer on the seed event, save JSON/tool trace.
3. Run Drift Analyzer treatment with memory retrieval enabled, save JSON.
4. Run a negative-control case, save JSON.
5. Use this script to verify that memory was created/updated, retrieved, and
   explicitly used only where expected.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from exc


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def pattern_ids(output: dict[str, Any]) -> set[str]:
    ids = set()
    ids.update(str(x) for x in as_list(output.get("retrieved_patterns_used")) if x)
    for item in as_list(output.get("retrieved_patterns")):
        if isinstance(item, dict) and item.get("pattern_id"):
            ids.add(str(item["pattern_id"]))
    return ids


def claim_diff_types(output: dict[str, Any]) -> set[str]:
    return {
        str(diff.get("diff_type"))
        for diff in as_list(output.get("claim_diffs"))
        if isinstance(diff, dict) and diff.get("diff_type")
    }


def output_text(output: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in ("drift_summary", "memory_usage_rationale", "rationale", "reasoning"):
        value = output.get(key)
        if isinstance(value, str):
            pieces.append(value)
    for diff in as_list(output.get("claim_diffs")):
        if isinstance(diff, dict):
            for key in ("change_description", "preprint_text", "published_text"):
                value = diff.get(key)
                if isinstance(value, str):
                    pieces.append(value)
    return "\n".join(pieces).lower()


def check(condition: bool, label: str, failures: list[str]) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"{status}: {label}")
    if not condition:
        failures.append(label)


def drift_input_by_role(cases_doc: dict[str, Any], role: str) -> dict[str, Any]:
    for case in cases_doc.get("cases", []):
        if case.get("role") == role:
            payload = case.get("drift_analyzer_input")
            if isinstance(payload, dict):
                return payload
    raise SystemExit(f"No drift_analyzer_input found for role={role}")


def roles_for_suite(suite: str) -> dict[str, str]:
    if suite == "v1":
        return {
            "seed": "seed_memory",
            "treatment": "treatment_similar",
            "negative": "negative_control",
        }
    if suite == "v2":
        return {
            "seed": "seed_memory_v2",
            "treatment": "treatment_similar_v2",
            "negative": "negative_control_v2",
        }
    raise SystemExit(f"Unsupported case suite: {suite}")


def prompt(title: str, instructions: list[str], payload: dict[str, Any]) -> str:
    lines = [title, "", "Important:"]
    lines.extend(f"- {item}" for item in instructions)
    lines.extend(["", "Input:", json.dumps(payload, indent=2), ""])
    return "\n".join(lines)


def init_run(args: argparse.Namespace) -> int:
    cases_path = Path(args.cases)
    cases_doc = load_json(cases_path)
    run_dir = Path(args.output_dir or f"agents/evals/results/memory-loop-ab-{date.today().isoformat()}")
    run_dir.mkdir(parents=True, exist_ok=True)
    roles = roles_for_suite(args.case_suite)

    baseline_payload = drift_input_by_role(cases_doc, roles["treatment"])
    seed_payload = drift_input_by_role(cases_doc, roles["seed"])
    treatment_payload = drift_input_by_role(cases_doc, roles["treatment"])
    negative_payload = drift_input_by_role(cases_doc, roles["negative"])

    if args.case_suite == "v2":
        treatment_instructions = [
            "Use memory retrieval normally.",
            "When calling search_drift_patterns, build query_text from preprint claims, published claims, and a structured drift descriptor inferred from this case, e.g. domain + clinical trial + outcome_switch + primary endpoint demoted to exploratory/secondary endpoint.",
            "Do not use a hard-coded AI diagnostic hint.",
            "Before finalizing retrieved_patterns_used, perform a relevance audit.",
            "Inspect all returned candidates, not only the highest-ranked one.",
            "Prefer patterns whose drift type matches outcome_switch or primary endpoint demotion.",
            "Use relevant pattern support_count and domain recurrence to calibrate severity.",
            "Return severity_calibration with baseline_materiality_without_memory, calibrated_materiality, calibration_delta, memory_pattern_ids, evidence, and rationale.",
            "If memory materially changes severity, make top-level materiality_score equal severity_calibration.calibrated_materiality.",
            "Do not put materiality_score inside individual claim_diffs; materiality_score belongs only at the top level.",
            "Return JSON only.",
        ]
        negative_instructions = [
            "Use memory retrieval normally.",
            "Only use a pattern if domain, drift type, and phenomenon are relevant.",
            "Do not use primary-outcome-switch memory for a cosmetic copy-edit or unit-formatting case.",
            "Return severity_calibration; if no memory is relevant, memory_pattern_ids and evidence must be [].",
            "Return JSON only.",
        ]
    else:
        treatment_instructions = [
            "Use memory retrieval normally.",
            "When calling search_drift_patterns, build query_text from preprint claims, published claims, and an inferred hint: AI diagnostic tool claim_disappearance quantitative performance metrics removed.",
            "Before finalizing retrieved_patterns_used, perform a relevance audit.",
            "Inspect all returned candidates, not only the highest-ranked one.",
            "Prefer patterns whose domain matches AI / machine learning / diagnostic tools.",
            "Prefer patterns whose drift type matches claim_disappearance.",
            "Prefer patterns describing quantitative performance metrics disappearing from preprint to publication.",
            "Do NOT use pharmacology, biochemistry, clinical genetics, hedging-addition, or effect-size-reduction patterns unless they directly match this case.",
            "If a retrieved pattern is relevant, include its pattern_id in retrieved_patterns_used and explain how it affected your reasoning.",
            "Do not put materiality_score inside individual claim_diffs; materiality_score belongs only at the top level.",
            "Return JSON only.",
        ]
        negative_instructions = [
            "Use memory retrieval normally.",
            "Only use a pattern if domain, drift type, and phenomenon are relevant.",
            "Do not use diagnostic-tool memory for an agriculture/yield case.",
            "Return JSON only.",
        ]

    files = {
        "baseline_prompt.md": prompt(
            "Run Drift Analyzer on the following case.",
            [
                "This is the BASELINE run.",
                "Do NOT use memory retrieval.",
                "Do NOT call search_drift_patterns.",
                "retrieved_patterns_used must be [].",
                "Return JSON only.",
            ],
            baseline_payload,
        ),
        "seed_drift_analyzer_prompt.md": prompt(
            "Run Drift Analyzer on the following case.",
            [
                "Use memory retrieval normally.",
                "Return JSON only.",
            ],
            seed_payload,
        ),
        "memory_synthesizer_prompt.md": "\n".join(
            [
                "Run Memory Synthesizer on the following drift event.",
                "",
                "Important:",
                "- Create or update a reusable drift pattern if appropriate.",
                "- Return JSON only.",
                "",
                "Input:",
                "{paste seed_drift_event.json here}",
                "",
            ]
        ),
        "treatment_prompt.md": prompt(
            "Run Drift Analyzer on the following case.",
            treatment_instructions,
            treatment_payload,
        ),
        "negative_prompt.md": prompt(
            "Run Drift Analyzer on the following case.",
            negative_instructions,
            negative_payload,
        ),
    }

    for name, content in files.items():
        (run_dir / name).write_text(content)

    readme = f"""# Memory Loop A/B Run

Generated from `{cases_path}` using case suite `{args.case_suite}`.

Fill these files with captured agent JSON outputs:

- `baseline.json`
- `seed_drift_event.json`
- `memory.json`
- `treatment.json`
- `negative.json`
- `score.txt`

Prompt files:

- `baseline_prompt.md`: send to a no-memory LLM or no-memory Drift Analyzer.
- `seed_drift_analyzer_prompt.md`: send to `drift_analyzer`.
- `memory_synthesizer_prompt.md`: paste `seed_drift_event.json`, then send to `memory_synthesizer`.
- `treatment_prompt.md`: send to `drift_analyzer`.
- `negative_prompt.md`: send to `drift_analyzer`.

Score after the JSON files are saved:

```bash
python3 agents/scripts/memory_loop_ab_eval.py score-run --run-dir {run_dir}
```
"""
    (run_dir / "README.md").write_text(readme)

    print(f"Created run directory: {run_dir}")
    for name in sorted(files):
        print(f"- {run_dir / name}")
    print(f"- {run_dir / 'README.md'}")
    return 0


def score(args: argparse.Namespace) -> int:
    baseline = load_json(Path(args.baseline))
    treatment = load_json(Path(args.treatment))
    negative = load_json(Path(args.negative)) if args.negative else None
    memory = load_json(Path(args.memory)) if args.memory else None

    expected_pattern_id = args.expected_pattern_id
    if not expected_pattern_id and memory and isinstance(memory.get("pattern"), dict):
        expected_pattern_id = memory["pattern"].get("pattern_id")
    failures: list[str] = []

    print(f"Experiment: {args.experiment_id}")
    print("")

    baseline_patterns = pattern_ids(baseline)
    treatment_patterns = pattern_ids(treatment)

    check(
        not baseline_patterns,
        "baseline does not use retrieved_patterns",
        failures,
    )
    check(
        bool(treatment_patterns),
        "treatment uses at least one retrieved pattern",
        failures,
    )
    if expected_pattern_id:
        check(
            expected_pattern_id in treatment_patterns,
            f"treatment uses expected pattern_id {expected_pattern_id}",
            failures,
        )

    treatment_text = output_text(treatment)
    memory_words = ["pattern", "memory", "prior", "similar", "historical", "previous"]
    check(
        any(word in treatment_text for word in memory_words),
        "treatment output explicitly explains memory influence",
        failures,
    )

    baseline_types = claim_diff_types(baseline)
    treatment_types = claim_diff_types(treatment)
    check(
        bool(treatment_types),
        "treatment emits claim_diffs with diff_type",
        failures,
    )
    if baseline_types:
        check(
            treatment_types == baseline_types or bool(treatment_types & baseline_types),
            "treatment diff_type remains comparable to baseline",
            failures,
        )

    score_value = treatment.get("materiality_score")
    check(
        isinstance(score_value, (int, float)) and 0 <= float(score_value) <= 1,
        "treatment materiality_score is numeric in [0, 1]",
        failures,
    )

    min_delta = getattr(args, "min_materiality_delta", None)
    if min_delta is not None:
        baseline_score = baseline.get("materiality_score")
        treatment_score = treatment.get("materiality_score")
        calibration = treatment.get("severity_calibration")
        check(
            isinstance(calibration, dict),
            "treatment includes severity_calibration",
            failures,
        )
        calibration_delta = calibration.get("calibration_delta") if isinstance(calibration, dict) else None
        check(
            isinstance(calibration_delta, (int, float)),
            "severity_calibration.calibration_delta is numeric",
            failures,
        )
        if isinstance(baseline_score, (int, float)) and isinstance(treatment_score, (int, float)):
            observed_delta = float(treatment_score) - float(baseline_score)
            check(
                observed_delta >= float(min_delta),
                f"treatment materiality_score exceeds baseline by at least {min_delta}",
                failures,
            )
        if isinstance(calibration_delta, (int, float)):
            check(
                float(calibration_delta) >= float(min_delta),
                f"severity_calibration.calibration_delta >= {min_delta}",
                failures,
            )

    if getattr(args, "strict_fields", False):
        outputs = [("baseline", baseline), ("treatment", treatment)]
        if negative:
            outputs.append(("negative", negative))
        for label, output in outputs:
            check(
                output.get("event_id") is None and output.get("analyzed_at") is None,
                f"{label} does not invent event_id/analyzed_at",
                failures,
            )
            nested_materiality = any(
                isinstance(diff, dict) and "materiality_score" in diff
                for diff in as_list(output.get("claim_diffs"))
            )
            check(
                not nested_materiality,
                f"{label} claim_diffs do not contain nested materiality_score",
                failures,
            )

    if memory:
        action = memory.get("action")
        pattern = memory.get("pattern") if isinstance(memory.get("pattern"), dict) else {}
        check(
            action in {"create_new", "update_existing"},
            "memory_synthesizer action is create_new or update_existing",
            failures,
        )
        check(
            bool(pattern.get("pattern_id")),
            "memory_synthesizer output includes pattern.pattern_id",
            failures,
        )
        check(
            int(pattern.get("support_count", 0)) >= 1,
            "memory pattern support_count >= 1",
            failures,
        )
        check(
            bool(as_list(pattern.get("source_event_ids"))),
            "memory pattern has source_event_ids",
            failures,
        )
        if getattr(args, "strict_fields", False):
            source_event_ids = [str(x).lower() for x in as_list(pattern.get("source_event_ids"))]
            invalid_markers = ("not_found", "unknown", "placeholder", "fake")
            check(
                not any(any(marker in event_id for marker in invalid_markers) for event_id in source_event_ids),
                "memory pattern source_event_ids do not contain placeholder ids",
                failures,
            )
            check(
                memory.get("synthesized_at") is None,
                "memory_synthesizer does not invent synthesized_at",
                failures,
            )

    if negative:
        negative_patterns = pattern_ids(negative)
        if expected_pattern_id:
            check(
                expected_pattern_id not in negative_patterns,
                "negative control does not use treatment pattern_id",
                failures,
            )
        negative_text = output_text(negative)
        check(
            "diagnostic" not in negative_text or "agriculture" in negative_text,
            "negative control rationale is not dominated by diagnostic-tool memory",
            failures,
        )

    print("")
    if failures:
        print(f"Verdict: FAIL ({len(failures)} failed checks)")
        return 1
    print("Verdict: PASS")
    return 0


def score_run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    score_args = argparse.Namespace(
        experiment_id=args.experiment_id,
        baseline=str(run_dir / "baseline.json"),
        memory=str(run_dir / "memory.json"),
        treatment=str(run_dir / "treatment.json"),
        negative=str(run_dir / "negative.json"),
        expected_pattern_id=args.expected_pattern_id,
        min_materiality_delta=args.min_materiality_delta,
        strict_fields=args.strict_fields,
    )
    return score(score_args)


def show_cases(args: argparse.Namespace) -> int:
    cases_doc = load_json(Path(args.cases))
    for case in cases_doc.get("cases", []):
        payload = case.get("drift_analyzer_input")
        if not payload:
            continue
        print(f"--- {case['case_id']} ({case['role']}) ---")
        print(json.dumps(payload, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Score ClaimDrift memory-loop A/B artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("show-cases", help="Print Drift Analyzer input payloads from the fixture file.")
    show.add_argument("--cases", default="agents/evals/memory_loop_ab_cases.json")
    show.set_defaults(func=show_cases)

    init = subparsers.add_parser("init-run", help="Create a dated result directory with copy/paste prompts.")
    init.add_argument("--cases", default="agents/evals/memory_loop_ab_cases.json")
    init.add_argument("--output-dir", help="Directory for prompts and captured run artifacts.")
    init.add_argument("--case-suite", choices=["v1", "v2"], default="v1")
    init.set_defaults(func=init_run)

    score_parser = subparsers.add_parser("score", help="Score captured baseline/treatment JSON outputs.")
    score_parser.add_argument("--experiment-id", default="memory-loop-ab-v1")
    score_parser.add_argument("--baseline", required=True, help="Drift Analyzer output with memory disabled/empty.")
    score_parser.add_argument("--treatment", required=True, help="Drift Analyzer output with memory enabled.")
    score_parser.add_argument("--negative", help="Negative-control Drift Analyzer output.")
    score_parser.add_argument("--memory", help="Memory Synthesizer output for the seed case.")
    score_parser.add_argument("--expected-pattern-id", help="Pattern id expected to be used by treatment and not by negative control.")
    score_parser.add_argument("--min-materiality-delta", type=float, help="Require treatment materiality_score to exceed baseline by this amount.")
    score_parser.add_argument("--strict-fields", action="store_true", help="Reject invented machine fields, nested materiality_score, and placeholder source_event_ids.")
    score_parser.set_defaults(func=score)

    score_run_parser = subparsers.add_parser("score-run", help="Score a standard run directory.")
    score_run_parser.add_argument("--experiment-id", default="memory-loop-ab-v1")
    score_run_parser.add_argument("--run-dir", required=True, help="Directory containing baseline/memory/treatment/negative JSON files.")
    score_run_parser.add_argument("--expected-pattern-id", help="Override the pattern id expected in treatment output.")
    score_run_parser.add_argument("--min-materiality-delta", type=float, help="Require treatment materiality_score to exceed baseline by this amount.")
    score_run_parser.add_argument("--strict-fields", action="store_true", help="Reject invented machine fields, nested materiality_score, and placeholder source_event_ids.")
    score_run_parser.set_defaults(func=score_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
