"""Read the supervisor jsonl dump and show the exact part shapes of every
citation_finder event, including what function_response wraps. Goal: confirm
the supervisor bug where _extract_final_output returns the MCP wrapper
instead of the agent's final text JSON.
"""
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

    cf_events = [e for e in events if e.get("author") == "citation_finder"]
    print(f"=== {len(cf_events)} citation_finder events ===\n")

    for i, ev in enumerate(cf_events):
        parts = ((ev.get("content") or {}).get("parts")) or []
        print(f"--- event {i} (invocation_id={ev.get('invocation_id','?')[:30]}) parts={len(parts)} ---")
        for j, part in enumerate(parts):
            keys = list(part.keys())
            if "text" in part and part["text"]:
                snip = part["text"][:200].replace("\n", " | ")
                print(f"  part[{j}] text len={len(part['text'])} snippet={snip!r}")
            elif "function_call" in part:
                fc = part["function_call"]
                print(f"  part[{j}] function_call name={fc.get('name')} args={list((fc.get('args') or {}).keys())}")
            elif "function_response" in part:
                fr = part["function_response"]
                resp = fr.get("response") or {}
                print(f"  part[{j}] function_response name={fr.get('name')} response_keys={list(resp.keys())[:8]}")
                # The dispatcher bug: MCP wraps real result as {"content":[...], "isError":false}
                if "content" in resp:
                    content = resp["content"]
                    print(f"           response.content type={type(content).__name__} len={len(content) if hasattr(content,'__len__') else '?'}")
                    if isinstance(content, list) and content:
                        first = content[0]
                        if isinstance(first, dict):
                            print(f"           response.content[0] keys={list(first.keys())[:5]}")
                            if "text" in first:
                                snippet = str(first["text"])[:200].replace("\n", " | ")
                                print(f"           response.content[0].text snippet={snippet!r}")
                # Does this branch carry affected_citations?
                if "affected_citations" in resp:
                    print(f"           HAS affected_citations: len={len(resp['affected_citations'])}")
                else:
                    print(f"           NO affected_citations key at top level")
            else:
                print(f"  part[{j}] other keys={keys}")
        print()


if __name__ == "__main__":
    main()
