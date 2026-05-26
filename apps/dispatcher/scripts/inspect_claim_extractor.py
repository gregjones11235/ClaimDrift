"""Inspect claim_extractor events — why did verify_fix.py report 0 claims?"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", required=True)
    args = p.parse_args()

    with Path(args.in_path).open("r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    ce = [e for e in events if e.get("author") == "claim_extractor"]
    print(f"=== {len(ce)} claim_extractor events ===\n")

    for i, ev in enumerate(ce):
        inv = ev.get("invocation_id", "?")[:30]
        parts = ((ev.get("content") or {}).get("parts")) or []
        print(f"--- event {i} inv={inv} parts={len(parts)} ---")
        for j, part in enumerate(parts):
            if "text" in part and part["text"]:
                t = part["text"]
                print(f"  part[{j}] text len={len(t)}")
                print(f"    {t[:1500]}")
            else:
                keys = list(part.keys())
                print(f"  part[{j}] non-text keys={keys}")
        print()


if __name__ == "__main__":
    main()
