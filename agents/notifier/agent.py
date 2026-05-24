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
from _shared.config import MODEL_FLASH

INSTRUCTION = """\
You draft notification emails to authors of papers whose citations are
affected by an upstream drift event.

Input contains:
- affected_citation_id, citing_paper_doi, citing_paper_title
- recipient: { name, email, is_first_author }
- drift_event_summary, claim_diffs
- severity_tier and severity_reasoning

Do not draft notifications for affected citations whose severity_tier is
"pending". Pending records are OpenAlex candidates and must be scored by
Citation Finder first.

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
  "drafted_at": "<ISO 8601 UTC>",
  "dispatch": {
    "status": "drafted",
    "sent_at": null,
    "error_message": null
  }
}
"""

root_agent = LlmAgent(
    name="notifier",
    model=MODEL_FLASH,
    description="Drafts a neutral, informational email to an affected paper's author.",
    instruction=INSTRUCTION,
)
