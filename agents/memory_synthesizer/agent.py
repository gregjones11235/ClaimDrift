"""
Memory Synthesizer — distills drift events into reusable patterns.
                     The WRITE side of the memory loop. Project's #1 selling point.

Owns:    contracts.md §3.5 Memory Synthesizer input/output
Reads:   `drift_events`, `drift_patterns` (to decide create vs update)
Writes:  `drift_patterns` index — read back by Drift Analyzer next time
Tools:   v0 has none — existing_similar_patterns is passed in via input.
         v1 will gain an Elastic MCP tool to do its own ELSER + BM25
         hybrid search on `drift_patterns` (top-5, threshold 0.75).

Fires ASYNC after Drift Analyzer writes a new drift_event. Non-blocking.
"""

from google.adk.agents import LlmAgent
from _shared.config import MODEL_PRO

INSTRUCTION = """\
You distill drift events into reusable patterns. This is the write side of
the project's memory loop — what you produce will be retrieved by future
Drift Analyzer runs to inform their reasoning.

Input contains:
- trigger, drift_event_id
- drift_event: the full drift_event object
- affected_citations_summary: { total_affected, central_count, comparative_count, peripheral_count }
- existing_similar_patterns: list of patterns already in the index with
  similarity_score; if any has score >= 0.75 you should UPDATE the highest-scoring
  one (action="update_existing"), otherwise CREATE a new pattern.

When creating a pattern_description (CRITICAL — this is the field that future
retrieval depends on):
- Must be a REUSABLE summary, not a description of this one event
- Must include: domain + type of drift + rough magnitude
- Must NOT include specific DOIs or author names
- 30-80 words, English

Pattern_type must be one of:
"numerical_softening" | "hedging_addition" | "claim_disappearance" |
"effect_size_reduction" | "other"

Return ONLY a JSON object matching contracts.md §3.5.2:
{
  "action": "create_new" | "update_existing",
  "pattern": {
    "pattern_id": "<new uuid if create_new; reuse old id if update_existing>",
    "pattern_description": "...",
    "pattern_type": "...",
    "domain_tags": [ "..." ],
    "source_event_ids": [ "..." ],
    "support_count": <int — 1 on create, prior_count+1 on update>,
    "created_at": "<ISO 8601 UTC>",
    "last_updated_at": "<ISO 8601 UTC>"
  },
  "synthesized_at": "<ISO 8601 UTC>"
}
"""

root_agent = LlmAgent(
    name="memory_synthesizer",
    model=MODEL_PRO,
    description="Distills drift_events into reusable, retrievable drift patterns (memory loop write side).",
    instruction=INSTRUCTION,
)
