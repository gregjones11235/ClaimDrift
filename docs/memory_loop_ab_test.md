# ClaimDrift Memory Loop A/B Test

Last updated: 2026-05-28

This runbook defines a small, reproducible experiment for proving that the
ClaimDrift memory loop is doing more than displaying `drift_patterns` in the
frontend.

The claim we want to prove:

> A pattern synthesized from one drift event is retrieved and explicitly used by
> Drift Analyzer on a later, similar drift event.

## What Counts As Evidence

Seeing rows in `drift_patterns` is necessary, but not sufficient. It proves the
write side of the loop exists. The demo should also show the read/use side:

1. Memory Synthesizer creates or updates a `drift_patterns` row.
2. A later similar case retrieves the expected pattern.
3. Drift Analyzer echoes that pattern in `retrieved_patterns_used`.
4. Drift Analyzer explains how the retrieved pattern affected its reasoning.
5. An unrelated negative-control case does not use that same pattern.

## Experiment Shape

Use the fixture cases in:

```text
agents/evals/memory_loop_ab_cases.json
```

The v1 fixture contains:

- `seed_diagnostic_claim_disappearance`: diagnostic AI claims disappear at publication.
- `treatment_diagnostic_claim_disappearance`: similar diagnostic AI claim disappearance.
- `negative_agriculture_hedging`: unrelated agriculture/yield hedging case.

The seed and treatment cases are intentionally similar. The negative control is
intentionally different.

## Manual Agent Run Protocol

For a repeatable run directory with ready-to-copy prompts, start with:

```bash
python3 agents/scripts/memory_loop_ab_eval.py init-run \
  --output-dir agents/evals/results/memory-loop-ab-YYYY-MM-DD
```

This creates:

- `baseline_prompt.md`
- `seed_drift_analyzer_prompt.md`
- `memory_synthesizer_prompt.md`
- `treatment_prompt.md`
- `negative_prompt.md`
- `README.md`

Paste each prompt into the relevant LLM/ADK agent and save the captured JSON
outputs into the same directory as `baseline.json`, `seed_drift_event.json`,
`memory.json`, `treatment.json`, and `negative.json`.

### 1. Print The Case Payloads

```bash
python3 agents/scripts/memory_loop_ab_eval.py show-cases \
  --cases agents/evals/memory_loop_ab_cases.json
```

Copy each JSON payload into the appropriate agent run.

### 2. Baseline Run: Drift Analyzer Without Memory

Run Drift Analyzer on `treatment_diagnostic_claim_disappearance` with memory
disabled, or against a test cluster/index where `drift_patterns` is empty.

Save the Drift Analyzer JSON output as:

```text
/tmp/claimdrift-memory-ab/baseline.json
```

Expected baseline behavior:

- `retrieved_patterns_used` is empty.
- It still identifies the claim disappearance.
- It cannot cite a prior pattern as rationale.

### 3. Seed Memory: Memory Synthesizer Writes A Pattern

Run Drift Analyzer on `seed_diagnostic_claim_disappearance`, write the resulting
`drift_event`, then run Memory Synthesizer on that drift event.

Save the Memory Synthesizer JSON output as:

```text
/tmp/claimdrift-memory-ab/memory.json
```

Capture the returned `pattern.pattern_id`; use it as `EXPECTED_PATTERN_ID` in
the scoring step.

Also verify Elasticsearch:

```bash
curl -s -X POST "$ELASTIC_ENDPOINT/drift_patterns/_search?size=10&pretty=true" \
  -H "Authorization: ApiKey $ELASTIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "_source": [
      "pattern_id",
      "pattern_description",
      "pattern_type",
      "domain_tags",
      "support_count",
      "source_event_ids",
      "record_source",
      "created_at",
      "last_updated_at"
    ],
    "query": {
      "match_all": {}
    }
  }'
```

### 4. Treatment Run: Drift Analyzer With Memory Enabled

Run Drift Analyzer on `treatment_diagnostic_claim_disappearance` with normal
memory retrieval enabled.

Save the Drift Analyzer JSON output as:

```text
/tmp/claimdrift-memory-ab/treatment.json
```

Expected treatment behavior:

- `retrieved_patterns_used` includes `EXPECTED_PATTERN_ID`.
- The output explains that the diagnostic claim-disappearance pattern is relevant.
- The summary/materiality rationale is more specific than baseline.

### 5. Negative Control

Run Drift Analyzer on `negative_agriculture_hedging` with memory enabled.

Save the output as:

```text
/tmp/claimdrift-memory-ab/negative.json
```

Expected negative behavior:

- It should not include `EXPECTED_PATTERN_ID`.
- It should reason about agriculture/yield hedging, not diagnostic AI claim disappearance.

## Automated Scoring

After saving the four JSON files:

```bash
python3 agents/scripts/memory_loop_ab_eval.py score-run \
  --run-dir agents/evals/results/memory-loop-ab-YYYY-MM-DD
```

Or call the lower-level scorer directly:

```bash
python3 agents/scripts/memory_loop_ab_eval.py score \
  --baseline /tmp/claimdrift-memory-ab/baseline.json \
  --memory /tmp/claimdrift-memory-ab/memory.json \
  --treatment /tmp/claimdrift-memory-ab/treatment.json \
  --negative /tmp/claimdrift-memory-ab/negative.json \
  --expected-pattern-id "$EXPECTED_PATTERN_ID"
```

The script checks:

- baseline does not use memory,
- treatment uses memory,
- treatment uses the expected pattern id,
- treatment has a valid `materiality_score`,
- memory output has a valid action/pattern/support count,
- negative control does not use the treatment pattern id.

## Prompt-Tuning Interpretation

If Memory Synthesizer writes a good pattern but treatment does not retrieve it:

- improve `pattern_description`,
- add better domain tags,
- inspect the `search_drift_patterns` query text,
- consider whether the pattern is too narrow or too broad.

If treatment retrieves the pattern but does not use it:

- tune Drift Analyzer prompt to require an explicit memory-use rationale.
- require the output to say which `pattern_id` was used and how it affected
  `materiality_score` or `diff_type`.

If the negative control uses the diagnostic pattern:

- tune relevance filtering in the Drift Analyzer prompt.
- emphasize that retrieved candidates are not authoritative unless domain,
  drift type, and phenomenon all match.

## 2026-05-28 Prompt/Query A/B Finding

One captured run is stored in:

```text
agents/evals/results/memory-loop-ab-2026-05-28/
```

The successful run showed that relevance-audit prompt tuning alone was not
enough when the retrieval query was built only from preprint claims. The
expected diagnostic AI pattern did not appear in the top retrieved candidates.

The effective change was to tune Drift Analyzer's retrieval-query construction:

1. include preprint claim text;
2. include published claim text;
3. append an inferred drift/domain hint such as
   `AI diagnostic tool claim_disappearance quantitative performance metrics removed`.

After this change, the expected pattern
`c31f4560-611c-4335-a7a2-97b744d014da` was retrieved as the top candidate and
used in treatment output. The agriculture/yield negative control used
`probe-agri-001` and did not use the diagnostic AI pattern.

## Demo Video Narrative

The video can show:

1. Baseline: a similar diagnostic claim-disappearance case analyzed without memory.
2. Memory write: Memory Synthesizer creates/updates a reusable pattern.
3. Treatment: a later similar case retrieves and uses that pattern.
4. Negative control: an unrelated agriculture case does not use the diagnostic pattern.

That proves the loop as:

```text
drift_event -> drift_patterns -> retrieved_patterns_used -> improved analysis
```

rather than merely:

```text
drift_event -> drift_patterns visible in frontend
```
