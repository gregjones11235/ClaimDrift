"""drift_analyzer prompt v4a (§3.3). Text copied verbatim from data/cases/experiments/run.py (PROMPT_VERSION="v4a":
SYSTEM_BASE + the root-cause part of ROOT_CAUSE_GUIDE + SEVERITY_GUIDE_V4A). Do not edit without re-running the 33-case
evaluation: the accepted numbers in 新系统改造设计.md §8 were produced with exactly this text.
"""

PROMPT_VERSION = "v4a"

ROOT_CAUSES = ("definition_change | analysis_removed | reporting_choice | model_respecified | "
               "outcome_redefinition | method_change | generality_reversed | data_revision | wording_only")
ROOT_CAUSE_SET = {x.strip() for x in ROOT_CAUSES.split("|")}

SCHEMA_TEXT = f"""
Return ONLY a JSON object:
{{
  "drift_summary": "1-3 sentences",
  "claim_diffs": [
    {{
      "diff_type": "claim_disappeared | claim_added | numerical_shift | hedging_added | hedging_removed | claim_reversed | outcome_switch",
      "preprint_text": "<claim text from the preprint, or null>",
      "published_text": "<claim text from the published paper, or null>",
      "change_description": "<one sentence: what changed>",
      "root_cause": "<exactly one of: {ROOT_CAUSES}>",
      "materiality": <0.0-1.0; 0-0.3 minor cosmetic, 0.3-0.6 medium, 0.6-0.9 significant, 0.9-1.0 major: a claim a citing paper relied on no longer holds>,
      "evidence": [ {{"doc_id": "<preprint_vN | published>", "section": "<section title>", "quote": "<VERBATIM sentence copied from that document>"}} ],
      "rationale": "<why this root_cause and materiality; cite the evidence>"
    }}
  ],
  "materiality_score": <0.0-1.0 overall>
}}
Rules: quotes must be copied verbatim (they will be string-matched against the source; a paraphrase counts as fabricated).
materiality is about consequence for someone who cited the preprint claim, NOT about the size of the numeric change.
"""

SYSTEM_BASE = """You are a scientific claim-drift analyzer. You compare a preprint with its published journal version and report how the scientific claims changed and why.
The root_cause field is the heart of the job: the same abstract-level change can be (a) a definition or method change buried in Methods, (b) an analysis that was removed from the whole paper, (c) a mere change in how an unchanged number is reported, (d) a re-specified model whose numbers are not comparable, (e) a redefined primary outcome, (f) a reversed generality claim, (g) genuine data revision, or (h) wording only.
""" + SCHEMA_TEXT

ROOT_CAUSE_GUIDE = """
# How to choose root_cause (apply the tests in this order; the first that holds wins)
1. wording_only        — the two texts mean the same thing (e.g. "more than three" vs "at least four"; a renamed disease).
2. reporting_choice    — the underlying result is identical in BOTH full texts; only what the abstract selects, or how strongly it words it, changed
                         (a "new" number already in the preprint body; "partially rescued" -> "largely rescued" for the same measurement).
3. analysis_removed    — an estimate or analysis in the preprint has NO counterpart anywhere in the published paper (search the published
                         full text for its number and key terms: total_hits == 0 and no replacement estimate of the same quantity).
                         A vanished estimate is NOT data_revision: data_revision requires a revised value of the SAME quantity to exist.
4. outcome_redefinition — the headline outcome is a different quantity (e.g. basic -> full vaccination), even if the old one is still in a table.
5. definition_change   — same quantity, but the published Methods change its operational definition or inclusion/exclusion rule.
6. model_respecified   — the MODEL ITSELF asks a different question: its scenarios, structure or target changed, so preprint numbers have no
                         like-for-like counterpart (e.g. scenarios by outbreak extent replaced by scenarios by policy).
                         Re-running the same kind of model with different inputs, features, distribution or sample is NOT model_respecified.
7. method_change       — same question, same kind of analysis, but the procedure or inputs changed (feature set, fitted distribution,
                         truncation/adjustment, estimator) and the numbers moved as a result.
8. data_revision       — same question and method, more or corrected data; a revised value of the same quantity exists.
9. generality_reversed — a claim about consistency/universality is replaced by one about variability (or vice versa).

"""

_SEV_COMMON_SIG_MAJOR = """- 0.6-0.9 significant : a quantified headline estimate was withdrawn or substantially revised; a definition/method change moved a widely usable number;
                        a generality claim was reversed.
- 0.9-1.0 major       : the headline conclusion reversed, an actionable recommendation changed, or a central analysis was removed from the paper.
"""
SEVERITY_GUIDE_V4A = """
# How to score materiality (consequence for someone who cited the preprint claim; NOT the count or size of numeric edits)
Rule for STRENGTH-ONLY changes (data identical, only how strongly the conclusion is worded changed): score MEDIUM if the change alters
the kind of claim a citer can make -- causal <-> associational, established <-> possible/preliminary/needs further study, or a caveat
that limits the headline conclusion; otherwise MINOR.
- 0.0-0.3 minor       : wording_only; reporting_choice that leaves the kind of claim unchanged; revisions where every metric moves only slightly
                        and the conclusion is unchanged (many small edits are still minor).
- 0.3-0.6 medium      : a strength-only change that alters the kind of claim (rule above); or the headline quantity was redefined while the
                        like-for-like value barely moved.
""" + _SEV_COMMON_SIG_MAJOR

SYSTEM = SYSTEM_BASE + ROOT_CAUSE_GUIDE + SEVERITY_GUIDE_V4A

TIERS = ["minor", "medium", "significant", "major"]


def tier_of(materiality) -> str | None:
    """Full-text severity tier (v4a, four tiers) from a materiality score -- the same mapping the evaluation uses."""
    try:
        x = float(materiality)
    except (TypeError, ValueError):
        return None
    return TIERS[min(3, max(0, int(x // 0.3) if x < 0.9 else 3))]


def abstracts_block(pre_id: str, pre_abstract: str, pub_abstract: str) -> str:
    return (f"### PREPRINT ({pre_id}) abstract\n{pre_abstract}\n\n"
            f"### PUBLISHED abstract\n{pub_abstract}\n")


def stuffed_user(pre_id: str, pre, pub) -> str:
    return (abstracts_block(pre_id, pre.abstract, pub.abstract)
            + f"\n\n### PREPRINT ({pre_id}) FULL TEXT\n{pre.full_text()}\n\n### PUBLISHED FULL TEXT\n{pub.full_text()}\n"
            + "\nUse doc_id '%s' or 'published' in evidence.\n" % pre_id
            + "\nProduce the JSON now.")


def claims_user(pre_id: str, pre, pub, pre_claims_compact: str, pub_claims_compact: str) -> str:
    """Long-document branch (M0): abstracts + both claim lists, single call, no section reading."""
    base = (abstracts_block(pre_id, pre.abstract, pub.abstract)
            + f"\n### PREPRINT ({pre_id}) CLAIMS\n{pre_claims_compact}\n\n### PUBLISHED CLAIMS\n{pub_claims_compact}\n")
    note = ("You see extracted claims (verbatim sentences) and, if listed, full sections you asked to read. "
            f"Evidence quotes must be verbatim sentences from these, with doc_id '{pre_id}' or 'published'.")
    return base + "\n" + note + "\nProduce the JSON now."
