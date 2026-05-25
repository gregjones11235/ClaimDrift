"""
Notifier — drafts an email to the author of an affected citing paper.

Owns:    contracts.md §3.4 Notifier input/output
Reads:   nothing (gets fed the affected_citation by orchestrator)
Writes:  `notification_log` index
Tools:   v0 has none — the agent only DRAFTS the email and returns it as
         JSON. Actual dispatch (SMTP / Gmail API) is implemented by B
         and called by the orchestrator after we approve the draft.
         In v0 the dispatch field is always { status: "drafted" }.
"""

from google.adk.agents import LlmAgent

# Inlined from agents/_shared/config.py:MODEL_FLASH. The Agent Engine deploy
# packages only this subdirectory, so the `_shared` sibling package is not
# importable in the deployed runtime. Keep this constant in sync with
# _shared/config.py if it ever changes (it hasn't since v0). See
# agents/_DEPLOY_CHECKLIST.md Pitfall #1.
MODEL_FLASH = "gemini-2.5-flash"

INSTRUCTION = """\
You draft notification emails to authors of papers whose citations are
affected by an upstream drift event.

Input contains:
- affected_citation_id, citing_paper_doi, citing_paper_title
- recipient: { name, email, is_first_author }
- drift_event_summary, claim_diffs
- severity_tier and severity_reasoning

Tone requirements (NON-NEGOTIABLE):
- Neutral, informational. No lecturing. No blame.
- Quote the drifted claim verbatim AND the published version verbatim.
- Explain severity and why.
- Include explicit disclaimer: this is an automated detection notice;
  the author decides whether their work needs updating.
- 150-300 words. Address the recipient by name.

Return ONLY a JSON object matching contracts.md §3.4.2:
{
  "affected_citation_id": "...",
  "subject": "...",
  "body": "...",
  "reasoning_trace": "1-2 sentences on why you wrote it this way",
  "drafted_at": null,
  "dispatch": {
    "status": "drafted",
    "sent_at": null,
    "error_message": null
  }
}

Leave `drafted_at` as null — like `analyzed_at` in §3.2.2 and `synthesized_at`
in §3.5.2, this is a wall-clock field filled in by the orchestrating Cloud Run
dispatcher (§9.6.1) when it receives your output. LLMs cannot read a real
clock, and an invented timestamp is worse than null because it looks real.
"""

root_agent = LlmAgent(
    name="notifier",
    model=MODEL_FLASH,
    description="Drafts a neutral, informational email to an affected paper's author.",
    instruction=INSTRUCTION,
)
