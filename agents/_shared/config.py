"""
Shared configuration for all ClaimDrift agents.

Single source of truth for:
  - model ids (so we can swap 2.5-flash -> 3.5-flash in one place when ADK
    catches up; see Changelog in contracts.md)
  - any cross-agent constants

DO NOT put prompts here. Prompts live in each agent.py for now, and will
move to prompts/<agent_name>.md once A starts iterating on them.
"""

# Model selection — see contracts.md §1.1 and the gemini-3.5-flash note.
# Flash for fast / cheap / structured-extraction agents.
# Pro for agents that need real reasoning (Drift Analyzer, Memory Synthesizer).
MODEL_FLASH = "gemini-2.5-flash"
MODEL_PRO = "gemini-2.5-pro"

# Agent ids must match contracts.md §6.1 — these strings appear in SSE
# event payloads (agent_id field) and in the `name=` of each LlmAgent.
AGENT_IDS = {
    "claim_extractor",
    "drift_analyzer",
    "citation_finder",
    "notifier",
    "memory_synthesizer",
}
