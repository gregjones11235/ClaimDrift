"""T1 debug helper: re-run the supervisor stream locally and dump every event
to JSONL on disk for offline analysis with analyze_stream.py.

WARNING: running this fires a NEW live supervisor invocation. That writes
side effects:
  - memory_synthesizer self-calls update_drift_pattern / create_drift_pattern
    via MCP, so drift_patterns will change.
  - No drift_events / affected_citations / notification_log writes happen
    here (those are the dispatcher's job; we only stream-consume).

Only re-run when you need a FRESH event stream. Otherwise re-use the existing
JSONL with analyze_stream.py.

Usage (WSL + uv):
    cd ~/claim_drift/apps/dispatcher
    uv run --with vertexai --with 'google-cloud-aiplatform[agent-engines]' \\
        --with 'elasticsearch[async]' \\
        python scripts/replay_supervisor_stream.py \\
            --preprint-doi 10.1101/2024.05.03.24306688 \\
            --published-doi 10.1007/978-3-031-66535-6_19 \\
            --out tests/golden/stream_amblyopia_v2.jsonl

Prereq: GCP ADC done (`gcloud auth application-default login`) and the
dispatcher .env present with GCP_PROJECT / GCP_REGION /
SUPERVISOR_REASONING_ENGINE_ID / ELASTIC_ENDPOINT / ELASTIC_API_KEY.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import vertexai
from elasticsearch import AsyncElasticsearch
from vertexai import agent_engines


def _load_env_file(path: Path) -> None:
    if not path.exists():
        print(f"WARNING: env file {path} not found; relying on shell env")
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip()
        os.environ.setdefault(k.strip(), v)


async def fetch_doc(es: AsyncElasticsearch, doi: str) -> dict:
    resp = await es.search(
        index="preprints",
        size=1,
        query={"term": {"doi": doi}},
        sort=[{"is_final_preprint": "desc"}, {"posted_date": "desc"}],
    )
    hits = resp["hits"]["hits"]
    if not hits:
        raise SystemExit(f"preprint doi={doi} not found in ES")
    return dict(hits[0]["_source"])


async def main_async(args: argparse.Namespace) -> None:
    project = os.environ["GCP_PROJECT"]
    region = os.environ.get("GCP_REGION", "us-central1")
    supervisor_id = os.environ["SUPERVISOR_REASONING_ENGINE_ID"]

    es = AsyncElasticsearch(
        hosts=[os.environ["ELASTIC_ENDPOINT"]],
        api_key=os.environ["ELASTIC_API_KEY"],
        request_timeout=30,
    )
    try:
        preprint = await fetch_doc(es, args.preprint_doi)
        published = await fetch_doc(es, args.published_doi)
    finally:
        await es.close()

    def pick(d: dict) -> dict:
        return {k: d.get(k) for k in ("doi", "version", "title", "abstract", "conclusion")}

    envelope = {"preprint": pick(preprint), "published": pick(published)}
    print(
        f"envelope: preprint.doi={envelope['preprint']['doi']} v={envelope['preprint']['version']} "
        f"abstract_len={len(envelope['preprint'].get('abstract') or '')}"
    )
    print(
        f"          published.doi={envelope['published']['doi']} v={envelope['published']['version']} "
        f"abstract_len={len(envelope['published'].get('abstract') or '')}"
    )

    vertexai.init(project=project, location=region)
    supervisor = agent_engines.get(supervisor_id)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_events = 0
    authors: dict[str, int] = {}
    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as f:
        message = json.dumps(envelope, ensure_ascii=False)
        async for event in supervisor.async_stream_query(
            message=message,
            user_id=f"t1-debug::{args.preprint_doi}",
        ):
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()
            n_events += 1
            author = event.get("author", "?")
            authors[author] = authors.get(author, 0) + 1
            if n_events % 10 == 0:
                elapsed = time.monotonic() - t0
                print(f"  ...{n_events} events  {elapsed:.0f}s  authors={authors}")

    elapsed = time.monotonic() - t0
    print(f"\nDONE. {n_events} events in {elapsed:.1f}s -> {out_path}")
    print(f"author distribution: {authors}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--preprint-doi", required=True)
    p.add_argument("--published-doi", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    _load_env_file(Path(__file__).resolve().parents[1] / ".env")
    os.environ.pop("USE_STUB_STREAM", None)

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
