#!/usr/bin/env python3
"""Orchestrate the semi-automated memory-loop v2 A/B eval.

This runner intentionally leaves ADK/LLM invocation as the only manual step.
It automates everything around those calls: prompt directory creation,
artifact readiness checks, deterministic memory normalization, optional
Elasticsearch upsert, strict scoring, and demo summary generation.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_SCRIPT = REPO_ROOT / "agents" / "scripts" / "memory_loop_ab_eval.py"
UPSERT_SCRIPT = REPO_ROOT / "agents" / "scripts" / "upsert_eval_memory_pattern.py"

PATTERN_ID = "memory-loop-v2-outcome-switch-0101"
SOURCE_EVENT_ID = "memory-loop-v2-seed-0101"

REQUIRED_OUTPUTS = [
    "baseline.json",
    "seed_drift_event.json",
    "memory_raw.json",
    "memory.json",
    "treatment.json",
    "negative.json",
]


def _run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(cmd))
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected JSON object")
    return value


def _materiality(path: Path) -> float | None:
    if not path.exists():
        return None
    value = _load_json(path).get("materiality_score")
    return float(value) if isinstance(value, (int, float)) else None


def default_run_dir() -> Path:
    return REPO_ROOT / "agents" / "evals" / "results" / f"memory-loop-ab-v2-{date.today().isoformat()}"


def init_run(run_dir: Path) -> None:
    _run(
        [
            sys.executable,
            str(EVAL_SCRIPT),
            "init-run",
            "--case-suite",
            "v2",
            "--output-dir",
            str(run_dir.relative_to(REPO_ROOT) if run_dir.is_relative_to(REPO_ROOT) else run_dir),
        ]
    )


def check_artifacts(run_dir: Path) -> list[str]:
    missing = [name for name in REQUIRED_OUTPUTS if not (run_dir / name).exists()]
    if missing:
        print("Missing artifacts:")
        for name in missing:
            print(f"- {run_dir / name}")
    else:
        print("All expected v2 artifacts are present.")
    return missing


def normalize_memory(run_dir: Path) -> None:
    raw_path = run_dir / "memory_raw.json"
    output_path = run_dir / "memory.json"
    if not raw_path.exists():
        raise SystemExit(f"Cannot normalize: missing {raw_path}")
    _run(
        [
            sys.executable,
            str(EVAL_SCRIPT),
            "normalize-memory",
            "--input",
            str(raw_path),
            "--output",
            str(output_path),
            "--pattern-id",
            PATTERN_ID,
            "--source-event-id",
            SOURCE_EVENT_ID,
            "--pattern-type",
            "outcome_switch",
            "--support-count",
            "1",
            "--action",
            "create_new",
        ]
    )


def upsert_memory(run_dir: Path, *, apply: bool) -> None:
    memory_path = run_dir / "memory.json"
    if not memory_path.exists():
        raise SystemExit(f"Cannot upsert: missing {memory_path}")
    cmd = [
        sys.executable,
        str(UPSERT_SCRIPT),
        "--memory",
        str(memory_path),
    ]
    if apply:
        cmd.append("--apply")
    _run(cmd)


def score_run(run_dir: Path) -> str:
    for name in ("baseline.json", "memory.json", "treatment.json", "negative.json"):
        if not (run_dir / name).exists():
            raise SystemExit(f"Cannot score: missing {run_dir / name}")
    result = _run(
        [
            sys.executable,
            str(EVAL_SCRIPT),
            "score-run",
            "--experiment-id",
            "memory-loop-ab-v2",
            "--run-dir",
            str(run_dir),
            "--min-materiality-delta",
            "0.15",
            "--strict-fields",
        ],
        capture=True,
    )
    print(result.stdout, end="")
    (run_dir / "score.txt").write_text(result.stdout)
    return result.stdout


def write_summary(run_dir: Path) -> None:
    baseline_score = _materiality(run_dir / "baseline.json")
    treatment = _load_json(run_dir / "treatment.json") if (run_dir / "treatment.json").exists() else {}
    negative_score = _materiality(run_dir / "negative.json")
    memory = _load_json(run_dir / "memory.json") if (run_dir / "memory.json").exists() else {}

    treatment_score = treatment.get("materiality_score")
    calibration = treatment.get("severity_calibration")
    delta = calibration.get("calibration_delta") if isinstance(calibration, dict) else None
    memory_ids = calibration.get("memory_pattern_ids") if isinstance(calibration, dict) else []
    pattern = memory.get("pattern") if isinstance(memory.get("pattern"), dict) else {}

    summary = f"""# Memory Loop A/B v2 Demo Summary

## Result

- Baseline materiality: `{baseline_score}`
- Treatment materiality: `{treatment_score}`
- Calibration delta: `{delta}`
- Negative-control materiality: `{negative_score}`
- Memory pattern used for calibration: `{json.dumps(memory_ids)}`

## Memory Artifact

- pattern_id: `{pattern.get("pattern_id")}`
- pattern_type: `{pattern.get("pattern_type")}`
- support_count: `{pattern.get("support_count")}`

## Demo Beats

1. Show `baseline.json`: no retrieved memory, materiality stays below max severity.
2. Show `memory_raw.json` then `memory.json`: LLM proposes semantics; code owns ids/timestamps/schema.
3. Show `treatment.json`: retrieved outcome-switch memory calibrates severity upward.
4. Show `negative.json`: cosmetic/unit copy-edit does not misuse outcome-switch memory.
5. Show `score.txt`: strict scorer verdict.
"""
    path = run_dir / "demo_summary.md"
    path.write_text(summary)
    print(f"Wrote demo summary: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ClaimDrift memory-loop v2 eval workflow.")
    parser.add_argument("--run-dir", type=Path, default=default_run_dir())
    parser.add_argument("--init", action="store_true", help="Create prompt/readme files for a v2 run.")
    parser.add_argument("--check", action="store_true", help="Print missing/present artifacts.")
    parser.add_argument("--normalize", action="store_true", help="Normalize memory_raw.json into memory.json.")
    parser.add_argument("--upsert-dry-run", action="store_true", help="Prepare the ES upsert document without writing.")
    parser.add_argument("--apply-memory", action="store_true", help="Upsert memory.json into Elasticsearch.")
    parser.add_argument("--score", action="store_true", help="Run strict v2 scoring and write score.txt.")
    parser.add_argument("--summary", action="store_true", help="Write demo_summary.md.")
    parser.add_argument(
        "--all-local",
        action="store_true",
        help="Run local-only steps: init, check, normalize if possible, score if possible, summary if possible.",
    )
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()

    if args.all_local:
        args.init = True
        args.check = True
        args.normalize = (run_dir / "memory_raw.json").exists()
        args.score = all((run_dir / name).exists() for name in ("baseline.json", "memory.json", "treatment.json", "negative.json"))
        args.summary = args.score

    if not any(
        [
            args.init,
            args.check,
            args.normalize,
            args.upsert_dry_run,
            args.apply_memory,
            args.score,
            args.summary,
        ]
    ):
        parser.error("choose at least one action, e.g. --init, --check, --all-local")

    if args.init:
        init_run(run_dir)
    if args.check:
        check_artifacts(run_dir)
    if args.normalize:
        normalize_memory(run_dir)
    if args.upsert_dry_run:
        upsert_memory(run_dir, apply=False)
    if args.apply_memory:
        upsert_memory(run_dir, apply=True)
    if args.score:
        score_run(run_dir)
    if args.summary:
        write_summary(run_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
