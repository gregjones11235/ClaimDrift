# Pattern Curator — Operations Runbook

Last updated: 2026-05-31

The `pattern_curator` (C3 / D3, contracts.md §3.6) is an offline memory-governance
batch job: it incrementally scans `drift_patterns`, repairs data hygiene, evicts
empty rows, and merges duplicate patterns (one Gemini judgment per candidate
pair) to purify the base rates §3.2 severity calibration depends on. It is a
Cloud Run **Job** (batch, runs-to-completion), NOT an Agent Engine reasoning
engine — see [memory_loop_v2_design.md](memory_loop_v2_design.md) B.1.

## Operating model: dry-run by default, human-reviewed apply

**The scheduled daily run is DRY-RUN.** The image's default args
(`--max-judgments=50`, no `--apply`) make every scheduled execution *propose*
merges/evictions and log them, writing nothing. A human reviews the proposals in
the logs, then runs once with `--apply` to actually govern.

This safe-by-default model was adopted after the **2026-05-31 incident** (see
postmortem below). Rationale: merges are irreversible aggregations, so a write
should never happen unreviewed.

### Daily (automatic, dry-run)

Cloud Scheduler `claimdrift-pattern-curator-daily` triggers the Job once a day
(`0 4 * * *` America/New_York — the hour is arbitrary; the curator does not
contend with live retrieval because C1 isolated the ELSER endpoint). The run
proposes and logs; nothing is written.

### Reviewing proposals

```bash
# Find the latest execution name:
gcloud run jobs executions list --job=claimdrift-pattern-curator \
  --project tensile-topic-496519-i1 --region us-central1 --limit=1

# Read its report + proposed merges:
gcloud logging read \
  'resource.type=cloud_run_job AND labels."run.googleapis.com/execution_name"=<EXEC_NAME>' \
  --project tensile-topic-496519-i1 --freshness=2h \
  --format='value(timestamp, textPayload)' --order=asc
```

Look at the `merges proposed:` count and the `[PROPOSED] survivor -> loser` lines
with their rationale. Confirm each proposed merge is genuinely the same
phenomenon.

### Applying (after review)

**MANDATORY first: snapshot `drift_patterns` before any `--apply`.** Merges are
irreversible aggregations; a pre-governance snapshot is the only way to
reconstruct exact before/after accounting (the 2026-05-31 incident was
un-reconstructable precisely because no snapshot existed). One reindex:

```bash
SNAP="drift_patterns_snapshot_$(date +%Y%m%d_%H%M%S)"
curl -s -X POST "$ELASTIC_ENDPOINT/_reindex?wait_for_completion=true" \
  -H "Authorization: ApiKey $ELASTIC_API_KEY" -H "Content-Type: application/json" \
  -d "{\"source\":{\"index\":\"drift_patterns\"},\"dest\":{\"index\":\"$SNAP\"}}"
echo "snapshot -> $SNAP"   # delete it once you've confirmed the apply looks right
```

Then apply:

```bash
gcloud run jobs execute claimdrift-pattern-curator \
  --project tensile-topic-496519-i1 --region us-central1 \
  --args='--apply,--max-judgments=50' --wait
```

`--max-judgments=50` caps the per-run LLM judgments so a dense same-type index
cannot blow the Cloud Run task timeout (the incident root cause). If the report
says `judgments capped: True`, run again to continue with the remaining pairs.

## Deploy / redeploy

Two steps from the **repo root**, in WSL bash (the proven path, same as the
dispatcher):

```bash
# 1. build the image (Cloud Build, repo-root context)
gcloud builds submit . --config=agents/pattern_curator/cloudbuild.yaml
# 2. create/update the Job + daily (dry-run) Scheduler
bash agents/pattern_curator/scripts/deploy_curator_job.sh
```

Config (project/region/ELASTIC_ENDPOINT) is read automatically from
`agents/.env`. The runtime SA needs `roles/aiplatform.user` (the Gemini call) and
`roles/secretmanager.secretAccessor` (the `elastic-api-key` secret).

## Pause / resume the schedule

```bash
gcloud scheduler jobs pause  claimdrift-pattern-curator-daily --project tensile-topic-496519-i1 --location us-central1
gcloud scheduler jobs resume claimdrift-pattern-curator-daily --project tensile-topic-496519-i1 --location us-central1
```

## Safety properties (verified)

- **Writes are optimistic-concurrency guarded** (`if_seq_no`/`if_primary_term`):
  a curator merge can never clobber an in-flight `memory_synthesizer` append; a
  conflict is logged and the op defers to the next run.
- **Merges are lossless**: the loser's `source_event_ids` are unioned into the
  survivor and `support_count` is recomputed, so the global set of
  `source_event_ids` is conserved across a merge. The §3.5.1 invariant
  (`support_count == len(unique source_event_ids)`) holds on every row.
- **demo_seed rows are never evicted or merged** (recall excludes them).
- **Eviction is targeted** (explicit id set), never a broad delete.

### Health check (run any time)

Conservation + invariant check — proves no data was lost or corrupted:

```bash
curl -s -X POST "$ELASTIC_ENDPOINT/drift_patterns/_search?size=200" \
  -H "Authorization: ApiKey $ELASTIC_API_KEY" -H "Content-Type: application/json" \
  -d '{"_source":["pattern_id","support_count","source_event_ids"]}' \
  | python3 -c "
import sys,json
hits=json.load(sys.stdin)['hits']['hits']
allev=set(); mism=0
for h in hits:
    s=h['_source']; ev=s.get('source_event_ids') or []
    allev.update(ev)
    if (s.get('support_count') or 0)!=len(set(ev)): mism+=1
print('rows',len(hits),'unique_events',len(allev),'invariant_violations',mism)"
```

`invariant_violations` must be 0.

## Postmortem: 2026-05-31 unbounded-apply incident

**What happened.** During first-time deployment, the curator was run on real data
across four executions in ~90 minutes. The first image had **no `max_judgments`
cap**; its first task did a full `--apply` scan, ran for 30 minutes on a dense
same-`pattern_type` index (≈N×top_k serial Gemini judgments), and was killed by
the Cloud Run 1800s task timeout — after silently applying a batch of merges
whose `[APPLIED]` logs were lost to SIGKILL (no flush). Repeated manual runs
(including watermark-less full scans) merged further. `drift_patterns` went from
~81 rows to 35.

**Impact.** None to data integrity. A conservation check confirmed: 35 rows,
`sum(support_count)=389`, **263 unique `source_event_ids` preserved**, and **0
rows** violating `support_count == len(unique event_ids)`. Every merge was a
valid same-phenomenon aggregation (rationales inspected). The row-count drop is
the *intended* base-rate-purification effect (many duplicate rows of the dominant
`claim_disappearance` type collapsed into a few high-support survivors), only
larger and less controlled than intended.

**Root causes.**
1. Image defaulted to `--apply` with no judgment cap → a dense index drove an
   unbounded serial-LLM loop that timed out.
2. The run was driven repeatedly by hand on live data, with watermark behavior
   not yet understood, compounding the merge volume.
3. No dry-run-review gate before writes.

**Fixes (landed 2026-05-31).**
- `dedup.run_dedup` now (a) judges each unordered pair at most once
  (`judged_pairs` set) and (b) caps at `max_judgments` (default 50), deferring
  the rest and holding the watermark so leftovers are finished next run.
- Stage-level flushed progress logs (`[curator] …`, `[dedup] …`) so a long run is
  observable instead of silent.
- Image now defaults to **dry-run** (`CMD ["--max-judgments=50"]`, no `--apply`);
  the schedule proposes only, and `--apply` is a deliberate human step.
- Unit tests cover pair-dedup, the cap, and capped-watermark-hold
  (`pattern_curator/tests/test_dedup.py`, `test_curator.py`).

**Operating change.** Daily schedule = dry-run; apply is human-reviewed (this
runbook).
