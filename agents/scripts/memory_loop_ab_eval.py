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


def score(args: argparse.Namespace) -> int:
    baseline = load_json(Path(args.baseline))
    treatment = load_json(Path(args.treatment))
    negative = load_json(Path(args.negative)) if args.negative else None
    memory = load_json(Path(args.memory)) if args.memory else None

    expected_pattern_id = args.expected_pattern_id
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

    score_parser = subparsers.add_parser("score", help="Score captured baseline/treatment JSON outputs.")
    score_parser.add_argument("--experiment-id", default="memory-loop-ab-v1")
    score_parser.add_argument("--baseline", required=True, help="Drift Analyzer output with memory disabled/empty.")
    score_parser.add_argument("--treatment", required=True, help="Drift Analyzer output with memory enabled.")
    score_parser.add_argument("--negative", help="Negative-control Drift Analyzer output.")
    score_parser.add_argument("--memory", help="Memory Synthesizer output for the seed case.")
    score_parser.add_argument("--expected-pattern-id", help="Pattern id expected to be used by treatment and not by negative control.")
    score_parser.set_defaults(func=score)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
