"""Offline verification of the supervisor _extract_final_output fix
(T1, 2026-05-26).

Loads the supervisor jsonl dump, then runs the FIXED text-part-only extractor
against every sub-agent's events. Confirms:
  - drift_analyzer still parses (regression check)
  - citation_finder now returns the agent's §3.3.2 JSON (4 affected_citations)
    NOT the MCP function_response wrapper.
  - claim_extractor / memory_synthesizer still parse.

Re-runnable, no side effects.

Usage:
    uv run python scripts/verify_fix.py --in tests/golden/stream_amblyopia_v2.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def _strip_markdown_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _extract_final_output_fixed(events: list[dict]) -> dict | None:
    """Mirrors the post-fix supervisor _extract_final_output exactly."""
    for event in reversed(events):
        parts = ((event.get("content") or {}).get("parts")) or []
        combined = "".join(p.get("text", "") for p in parts if "text" in p)
        if not combined.strip():
            continue
        stripped = _strip_markdown_fence(combined)
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", required=True)
    args = p.parse_args()

    with Path(args.in_path).open("r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    by_author: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        by_author[ev.get("author", "?")].append(ev)

    expectations = {
        "claim_extractor": ("claims", "list"),
        "drift_analyzer": ("claim_diffs", "list"),
        "citation_finder": ("affected_citations", "list"),
        "memory_synthesizer": ("pattern", "dict"),
    }

    all_ok = True
    for author, (key, kind) in expectations.items():
        agent_events = by_author.get(author, [])
        out = _extract_final_output_fixed(agent_events)
        if out is None:
            print(f"  [FAIL] {author}: {len(agent_events)} events, no parseable text JSON")
            all_ok = False
            continue
        val = out.get(key)
        if val is None:
            print(f"  [FAIL] {author}: parsed but missing '{key}'  keys={sorted(out.keys())}")
            all_ok = False
            continue
        if kind == "list":
            print(f"  [OK]   {author}: {len(val)} {key}")
        else:
            print(f"  [OK]   {author}: {key} present (keys: {sorted(val.keys()) if isinstance(val, dict) else type(val).__name__})")

    # Critical assertion for the T1 bug specifically:
    citation_finder_out = _extract_final_output_fixed(by_author.get("citation_finder", []))
    ac = (citation_finder_out or {}).get("affected_citations") or []
    print()
    print(f"  T1 ASSERTION: supervisor would now see {len(ac)} affected_citations -> "
          f"notifier fan-out {'WOULD RUN' if ac else 'STILL SKIPPED'}")

    if not all_ok or not ac:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
