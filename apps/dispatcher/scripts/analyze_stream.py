"""T1 debug: parse a supervisor-stream JSONL dump and explain why the
dispatcher's notification_log ended up empty.

Pure offline analysis — no external services. Re-runnable against the same
JSONL with no side effects.

The dispatcher logic we mirror lives in apps/dispatcher/main.py:
    - extract_final_output (line 259)
    - extract_notifier_outputs (line 265)
    - _extract_from_events (line 236)

If this script's verdict matches what we saw in ES (notification_log = 0),
the JSONL is faithful and we know which branch failed.

Usage (WSL):
    uv run python apps/dispatcher/scripts/analyze_stream.py \\
        --in apps/dispatcher/tests/golden/stream_amblyopia_v2.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _strip_markdown_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _extract_from_events(events: list[dict[str, Any]]) -> dict | None:
    """Exact copy of apps/dispatcher/main.py:_extract_from_events."""
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


def load_events(path: Path) -> list[dict]:
    events = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def summarize_part_shape(part: dict) -> str:
    """One-letter tag for what kind of part this is."""
    if "text" in part and part.get("text"):
        return "T"
    if part.get("function_call"):
        return "C"
    if part.get("function_response"):
        return "R"
    return "?"


def analyze(events: list[dict]) -> None:
    print(f"\n=== TOTAL EVENTS: {len(events)} ===\n")

    # Q1: author distribution
    by_author: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        by_author[ev.get("author", "<missing>")].append(ev)

    print("--- Q1: events per author ---")
    for author, evs in sorted(by_author.items(), key=lambda x: -len(x[1])):
        print(f"  {author:25s} {len(evs):4d} events")
    print()

    # Q2: notifier invocation_id grouping
    notifier_events = by_author.get("notifier", [])
    print(f"--- Q2: notifier invocation_id grouping ({len(notifier_events)} events) ---")
    inv_groups: dict[str, list[dict]] = defaultdict(list)
    for ev in notifier_events:
        inv_groups[ev.get("invocation_id") or "<missing>"].append(ev)
    print(f"  distinct invocation_ids: {len(inv_groups)}")
    for inv_id, group in inv_groups.items():
        shapes = Counter(summarize_part_shape(p)
                         for ev in group
                         for p in (((ev.get("content") or {}).get("parts")) or []))
        print(f"    {inv_id[:40]:40s}  {len(group):3d} events  part_shapes={dict(shapes)}")
    print()

    # Q3: per-notifier-invocation part shape detail
    print("--- Q3: notifier part shapes per invocation (T=text C=function_call R=function_response) ---")
    for inv_id, group in inv_groups.items():
        per_event_shapes = []
        for ev in group:
            parts = ((ev.get("content") or {}).get("parts")) or []
            tag = "".join(summarize_part_shape(p) for p in parts) or "."
            per_event_shapes.append(tag)
        print(f"  {inv_id[:40]:40s}  shapes_in_order={per_event_shapes}")
    print()

    # Q4: simulate dispatcher's extract_notifier_outputs
    print("--- Q4: simulating dispatcher.extract_notifier_outputs ---")
    notifications: list[dict] = []
    for inv_id, group in inv_groups.items():
        out = _extract_from_events(group)
        if out is None:
            # find last text part for diagnostics
            last_text = ""
            for ev in reversed(group):
                parts = ((ev.get("content") or {}).get("parts")) or []
                for p in parts:
                    if p.get("text"):
                        last_text = p["text"]
                        break
                if last_text:
                    break
            print(f"  inv_id={inv_id[:40]:40s} -> NO PARSEABLE JSON")
            print(f"    last text part (first 300 chars): {last_text[:300]!r}")
            continue
        ac_id = out.get("affected_citation_id", "<missing>")
        subj = (out.get("subject") or "")[:60]
        print(f"  inv_id={inv_id[:40]:40s} -> ac_id={ac_id} subject={subj!r}")
        notifications.append(out)
    print(f"\n  RESULT: extract_notifier_outputs would return {len(notifications)} notifications")
    print(f"  (this matches what dispatcher would have written to notification_log)")
    print()

    # Bonus: also show drift_analyzer + citation_finder for cross-check
    for author in ("drift_analyzer", "citation_finder"):
        agent_events = by_author.get(author, [])
        out = _extract_from_events(agent_events)
        print(f"--- Bonus: extract_final_output('{author}') ---")
        if out is None:
            print(f"  NO PARSEABLE JSON ({len(agent_events)} events)")
        else:
            keys = sorted(out.keys())
            if author == "citation_finder":
                ac = out.get("affected_citations") or []
                print(f"  keys={keys}  affected_citations_len={len(ac)}")
            elif author == "drift_analyzer":
                diffs = out.get("claim_diffs") or []
                print(f"  keys={keys}  claim_diffs_len={len(diffs)} materiality={out.get('materiality_score')}")
        print()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", required=True, help="path to JSONL dump")
    args = p.parse_args()

    events = load_events(Path(args.in_path))
    analyze(events)


if __name__ == "__main__":
    main()
