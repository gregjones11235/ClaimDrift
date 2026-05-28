"""One-shot backfill: POST every eligible (preprint, published) pair from ES
to the dispatcher's /dispatch endpoint, bypassing the scheduled workflow.

Use this for bulk historical imports where the workflow's per-tick `size: 20`
+ `sort: desc` design would silently drop the tail of any high-density
ingestion window (see 2026-05-28 incident: 3067 backfilled pairs packed into
16.5h, workflow only dispatched ~39, watermark jumped past the rest).

What it does:
  1. Reads ELASTIC_ENDPOINT / ELASTIC_API_KEY from agents/.env (same as BFF).
  2. Reads dispatcher URL + WF_BEARER_TOKEN from apps/dispatcher/.env.
  3. Scrolls `preprints` for pairs matching the workflow's filters:
       published_doi exists, is_final_preprint=true,
       record_source != demo_seed, version != published,
       ingested_at in [--since, --until] (both optional, ISO-8601 UTC).
  4. POSTs each pair to dispatcher /dispatch serially with a delay.
     Dispatcher's idempotency check absorbs pairs that already have a
     drift_event (the 25 already-processed ones in the 2026-05-28 case),
     so re-running is safe. `?force=true` is NOT used.

Throttling:
  --delay-s controls inter-request spacing. Default 5s = 12 req/min ≈
  720 req/h. Dispatcher /dispatch returns in ~100ms (thin Pub/Sub enqueue),
  but the real bottleneck is Cloud Run /run handling ~200s per pipeline.
  At 5s spacing, 720/h enqueues will queue up; Pub/Sub absorbs them, but
  Cloud Run autoscaler must scale /run instances accordingly. If you don't
  know your --max-instances, start with --delay-s=20 (180/h) and watch.

Usage (WSL):
    cd ~/claim_drift && uv run --project agents \\
        python apps/dispatcher/scripts/backfill_dispatch_pairs.py \\
            --since 2026-05-25T11:00:00Z \\
            --until 2026-05-26T03:46:05Z \\
            --delay-s 12 \\
            --checkpoint /tmp/backfill.ckpt \\
            --resume

Resume after Ctrl-C / network blip: re-run the same command. With --resume
the script reads the checkpoint file and skips everything up to the last
successful POST. Dispatcher idempotency absorbs anything dispatched twice.

Use --dry-run to enumerate without POSTing.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / "agents" / ".env")
load_dotenv(REPO_ROOT / "apps" / "dispatcher" / ".env")


def es_request(method: str, path: str, body: dict | None = None) -> dict:
    import json
    endpoint = os.environ["ELASTIC_ENDPOINT"].rstrip("/")
    api_key = os.environ["ELASTIC_API_KEY"]
    req = urllib.request.Request(
        f"{endpoint}{path}",
        method=method,
        headers={
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode("utf-8") if body is not None else None,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def iter_eligible_pairs(
    since: str | None,
    until: str | None,
    resume_sort: list | None = None,
) -> Iterator[tuple[dict, list]]:
    """Yield (source, sort_values) per pair, ASC by (ingested_at, doi).

    `sort_values` is the ES search_after cursor for the yielded pair — pass
    it back as `resume_sort` on a later run to skip everything up to and
    including this pair.
    """
    range_clause: dict = {}
    if since:
        range_clause["gt"] = since
    if until:
        range_clause["lte"] = until

    query: dict = {
        "bool": {
            "filter": [
                {"exists": {"field": "published_doi"}},
                {"term": {"is_final_preprint": {"value": True}}},
            ],
            "must_not": [
                {"term": {"record_source": {"value": "demo_seed"}}},
                {"term": {"version": {"value": "published"}}},
            ],
        }
    }
    if range_clause:
        query["bool"]["filter"].append({"range": {"ingested_at": range_clause}})

    body = {
        "size": 500,
        "sort": [{"ingested_at": "asc"}, {"doi": "asc"}],
        "_source": ["doi", "published_doi", "ingested_at"],
        "query": query,
    }
    if resume_sort:
        body["search_after"] = resume_sort

    resp = es_request("POST", "/preprints/_search", body)
    hits = resp["hits"]["hits"]
    while hits:
        for h in hits:
            yield h["_source"], h["sort"]
        last_sort = hits[-1]["sort"]
        body["search_after"] = last_sort
        resp = es_request("POST", "/preprints/_search", body)
        hits = resp["hits"]["hits"]


def post_to_dispatcher(pair: dict, dispatcher_url: str, bearer: str, timeout_s: int) -> tuple[int, str]:
    import json
    payload = json.dumps({
        "preprint_doi": pair["doi"],
        "published_doi": pair["published_doi"],
    }).encode("utf-8")
    req = urllib.request.Request(
        dispatcher_url,
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        },
        data=payload,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:200]
    except urllib.error.URLError as e:
        return 0, f"URLError: {e}"
    except TimeoutError as e:
        return 0, f"TimeoutError after {timeout_s}s: {e}"
    except OSError as e:
        return 0, f"OSError: {e}"


def main() -> int:
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", help="ingested_at > (ISO-8601 UTC). Omit to start from epoch.")
    p.add_argument("--until", help="ingested_at <= (ISO-8601 UTC). Omit to read to now.")
    p.add_argument("--delay-s", type=float, default=20.0, help="seconds between POSTs (default 20)")
    p.add_argument("--dispatcher-url", default=None, help="override dispatcher /dispatch URL")
    p.add_argument(
        "--timeout-s",
        type=int,
        default=60,
        help="per-request timeout. Default 60s covers Cloud Run cold start "
             "plus dispatcher's internal 15s Pub/Sub publish wait. "
             "Don't drop below 30s.",
    )
    p.add_argument("--limit", type=int, default=0, help="stop after N pairs (0 = no limit)")
    p.add_argument("--dry-run", action="store_true", help="enumerate without POSTing")
    p.add_argument(
        "--checkpoint",
        default=None,
        help="path to checkpoint file. Written after each successful POST; "
             "read on startup if --resume is also set. Default: skipped (no resume).",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="if --checkpoint exists, skip everything up to and including the "
             "last recorded pair. Combine with the same --since/--until as the "
             "original run.",
    )
    args = p.parse_args()

    dispatcher_url = args.dispatcher_url or (
        "https://claimdrift-dispatcher-3gz4czm2hq-uc.a.run.app/dispatch"
    )
    bearer = os.environ.get("WF_BEARER_TOKEN")
    if not args.dry_run and not bearer:
        print("ERROR: WF_BEARER_TOKEN not set. Check apps/dispatcher/.env", file=sys.stderr)
        return 2

    resume_sort: list | None = None
    if args.resume and args.checkpoint and Path(args.checkpoint).exists():
        cp = json.loads(Path(args.checkpoint).read_text())
        resume_sort = cp.get("sort")
        print(f"Resuming from checkpoint: {args.checkpoint}")
        print(f"  last processed:    {cp.get('ingested_at')} {cp.get('doi')}")
        print(f"  prior counters:    enqueued={cp.get('n_ok',0)} "
              f"idempotent={cp.get('n_idempotent',0)} errors={cp.get('n_err',0)}")
        print()

    print(f"Backfill window: since={args.since!r} until={args.until!r}")
    print(f"Dispatcher URL:  {dispatcher_url}")
    print(f"Delay between POSTs: {args.delay_s}s")
    print(f"Dry run:         {args.dry_run}")
    if args.checkpoint:
        print(f"Checkpoint file: {args.checkpoint}")
    print()

    n_total = 0
    n_ok = 0
    n_idempotent = 0
    n_err = 0
    failed: list[dict] = []
    t_start = time.monotonic()

    retry_path = Path(args.checkpoint + ".retry") if args.checkpoint else None

    for pair, sort_values in iter_eligible_pairs(args.since, args.until, resume_sort):
        n_total += 1
        if args.limit and n_total > args.limit:
            n_total -= 1
            break

        if args.dry_run:
            print(f"[{n_total:>5}] {pair['ingested_at']}  {pair['doi']}  ->  {pair['published_doi']}")
            continue

        status, body = post_to_dispatcher(pair, dispatcher_url, bearer, args.timeout_s)
        if status in (200, 202):
            if "already_processed" in body:
                n_idempotent += 1
                tag = "skip"
            else:
                n_ok += 1
                tag = "enqu"
        else:
            n_err += 1
            tag = f"ERR{status}"
            failed.append({"pair": pair, "status": status, "body": body[:200]})
            if retry_path:
                with retry_path.open("a") as f:
                    f.write(json.dumps({"pair": pair, "status": status, "body": body[:200]}) + "\n")

        print(f"[{n_total:>5}] {tag}  {pair['ingested_at']}  {pair['doi']}  ({body[:80]})")

        # Always advance the checkpoint (success OR failure). Failures are
        # captured in retry_path so they aren't silently dropped — we just
        # don't want a single timeout to block the whole queue.
        if args.checkpoint:
            Path(args.checkpoint).write_text(json.dumps({
                "ingested_at": pair["ingested_at"],
                "doi": pair["doi"],
                "sort": sort_values,
                "n_ok": n_ok,
                "n_idempotent": n_idempotent,
                "n_err": n_err,
            }))

        time.sleep(args.delay_s)

    elapsed = time.monotonic() - t_start
    print()
    print(f"Main pass done in {elapsed:.0f}s.")
    print(f"  scanned:     {n_total}")
    if not args.dry_run:
        print(f"  enqueued:    {n_ok}")
        print(f"  idempotent:  {n_idempotent}")
        print(f"  errors:      {n_err}")

    if failed and not args.dry_run:
        print()
        print(f"Retrying {len(failed)} failed pair(s) with 2x timeout, 2x delay...")
        retry_timeout = args.timeout_s * 2
        retry_delay = args.delay_s * 2
        n_retry_ok = 0
        n_retry_err = 0
        still_failed: list[dict] = []
        for i, item in enumerate(failed, start=1):
            pair = item["pair"]
            status, body = post_to_dispatcher(pair, dispatcher_url, bearer, retry_timeout)
            if status in (200, 202):
                n_retry_ok += 1
                tag = "skip" if "already_processed" in body else "enqu"
            else:
                n_retry_err += 1
                still_failed.append({"pair": pair, "status": status, "body": body[:200]})
                tag = f"ERR{status}"
            print(f"[retry {i:>4}/{len(failed)}] {tag}  {pair['ingested_at']}  {pair['doi']}  ({body[:80]})")
            time.sleep(retry_delay)

        print()
        print(f"Retry pass: {n_retry_ok} recovered, {n_retry_err} still failed.")
        if still_failed and retry_path:
            still_path = Path(args.checkpoint + ".still_failed")
            still_path.write_text("\n".join(json.dumps(x) for x in still_failed))
            print(f"Persistent failures written to {still_path} — inspect dispatcher logs and re-run manually.")
        return 0 if n_retry_err == 0 else 1

    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
