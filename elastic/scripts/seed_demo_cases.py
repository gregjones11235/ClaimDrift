import json
from datetime import datetime, timezone
from pathlib import Path


OUT = Path(__file__).resolve().parents[1] / "demo_seed"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(rows, indent=2) + "\n")


def main() -> None:
    ts = now()

    preprints = [
        {
            "doi": "10.1101/2024.01.15.123456",
            "source": "biorxiv",
            "version": "v3",
            "is_final_preprint": True,
            "published_doi": "10.1016/j.cell.2024.05.001",
            "title": "Hydroxychloroquine and viral load dynamics in early COVID-19",
            "abstract": "Hydroxychloroquine reduced viral load by 45% in early COVID-19 patients.",
            "conclusion": "The treatment reduced viral load substantially in the studied cohort.",
            "authors": [{"name": "Demo Author", "orcid": None, "affiliation": "Demo Lab"}],
            "posted_date": "2024-01-15T00:00:00Z",
            "ingested_at": ts
        },
        {
            "doi": "10.1016/j.cell.2024.05.001",
            "source": "biorxiv",
            "version": "published",
            "is_final_preprint": False,
            "published_doi": "10.1016/j.cell.2024.05.001",
            "title": "Hydroxychloroquine and viral load dynamics in early COVID-19",
            "abstract": "Hydroxychloroquine was associated with a 12% viral load reduction, with uncertainty across subgroups.",
            "conclusion": "The treatment may reduce viral load modestly in selected patients.",
            "authors": [{"name": "Demo Author", "orcid": None, "affiliation": "Demo Lab"}],
            "posted_date": "2024-05-01T00:00:00Z",
            "ingested_at": ts
        },
        {
            "doi": "10.1101/2024.02.20.654321",
            "source": "medrxiv",
            "version": "v2",
            "is_final_preprint": True,
            "published_doi": "10.1056/demo.2024.002",
            "title": "Remote blood-pressure monitoring after hospital discharge",
            "abstract": "Remote monitoring reduced 30-day readmissions by 31% after discharge for hypertensive patients.",
            "conclusion": "Remote monitoring meaningfully lowered short-term readmission risk.",
            "authors": [{"name": "Example Clinician", "orcid": None, "affiliation": "Demo Hospital"}],
            "posted_date": "2024-02-20T00:00:00Z",
            "ingested_at": ts
        },
        {
            "doi": "10.1056/demo.2024.002",
            "source": "medrxiv",
            "version": "published",
            "is_final_preprint": False,
            "published_doi": "10.1056/demo.2024.002",
            "title": "Remote blood-pressure monitoring after hospital discharge",
            "abstract": "Remote monitoring was associated with an 8% readmission reduction, and the confidence interval crossed no effect.",
            "conclusion": "The intervention may help selected patients, but evidence for broad readmission reduction was limited.",
            "authors": [{"name": "Example Clinician", "orcid": None, "affiliation": "Demo Hospital"}],
            "posted_date": "2024-06-12T00:00:00Z",
            "ingested_at": ts
        }
    ]

    claims = [
        {
            "claim_id": "10.1101/2024.01.15.123456::v3::abstract::0",
            "parent_doi": "10.1101/2024.01.15.123456",
            "parent_version": "v3",
            "section": "abstract",
            "claim_idx": 0,
            "text": "Hydroxychloroquine reduced viral load by 45% in early COVID-19 patients.",
            "claim_type": "quantitative",
            "numerical_values": [{"metric": "viral_load_reduction", "value": 45.0, "unit": "percent", "comparison": "reduction"}],
            "hedging_level": "none",
            "extracted_at": ts
        },
        {
            "claim_id": "10.1016/j.cell.2024.05.001::published::abstract::0",
            "parent_doi": "10.1016/j.cell.2024.05.001",
            "parent_version": "published",
            "section": "abstract",
            "claim_idx": 0,
            "text": "Hydroxychloroquine was associated with a 12% viral load reduction, with uncertainty across subgroups.",
            "claim_type": "quantitative",
            "numerical_values": [{"metric": "viral_load_reduction", "value": 12.0, "unit": "percent", "comparison": "reduction"}],
            "hedging_level": "strong",
            "extracted_at": ts
        },
        {
            "claim_id": "10.1101/2024.02.20.654321::v2::abstract::0",
            "parent_doi": "10.1101/2024.02.20.654321",
            "parent_version": "v2",
            "section": "abstract",
            "claim_idx": 0,
            "text": "Remote monitoring reduced 30-day readmissions by 31% after discharge for hypertensive patients.",
            "claim_type": "quantitative",
            "numerical_values": [{"metric": "readmission_reduction", "value": 31.0, "unit": "percent", "comparison": "reduction"}],
            "hedging_level": "none",
            "extracted_at": ts
        },
        {
            "claim_id": "10.1056/demo.2024.002::published::abstract::0",
            "parent_doi": "10.1056/demo.2024.002",
            "parent_version": "published",
            "section": "abstract",
            "claim_idx": 0,
            "text": "Remote monitoring was associated with an 8% readmission reduction, and the confidence interval crossed no effect.",
            "claim_type": "quantitative",
            "numerical_values": [{"metric": "readmission_reduction", "value": 8.0, "unit": "percent", "comparison": "reduction"}],
            "hedging_level": "strong",
            "extracted_at": ts
        }
    ]

    drift_events = [
        {
            "event_id": "demo-drift-001",
            "preprint_doi": "10.1101/2024.01.15.123456",
            "preprint_version_compared": "v3",
            "published_doi": "10.1016/j.cell.2024.05.001",
            "detected_at": ts,
            "drift_summary": "Effect size for viral load reduction changed from 45% to 12%, and strong hedging was added.",
            "claim_diffs": [
                {
                    "diff_type": "numerical_shift",
                    "preprint_claim_id": "10.1101/2024.01.15.123456::v3::abstract::0",
                    "published_claim_id": "10.1016/j.cell.2024.05.001::published::abstract::0",
                    "preprint_text": "Hydroxychloroquine reduced viral load by 45% in early COVID-19 patients.",
                    "published_text": "Hydroxychloroquine was associated with a 12% viral load reduction, with uncertainty across subgroups.",
                    "change_description": "Effect size reduced from 45% to 12% and hedging was added.",
                    "numerical_delta": {
                        "metric": "viral_load_reduction",
                        "preprint_value": 45.0,
                        "published_value": 12.0,
                        "absolute_delta": -33.0,
                        "relative_delta": -0.733
                    }
                }
            ],
            "materiality_score": 0.82,
            "retrieved_patterns": [
                {
                    "pattern_id": "pattern-demo-001",
                    "pattern_description": "COVID-related clinical preprints may show large effect-size reductions at publication with added hedging.",
                    "pattern_type": "effect_size_reduction",
                    "domain_tags": ["covid-19", "clinical-trial"],
                    "support_count": 3,
                    "similarity_score": 0.84
                }
            ]
        },
        {
            "event_id": "demo-drift-002",
            "preprint_doi": "10.1101/2024.02.20.654321",
            "preprint_version_compared": "v2",
            "published_doi": "10.1056/demo.2024.002",
            "detected_at": ts,
            "drift_summary": "A readmission reduction claim softened from 31% to 8%, with uncertainty strong enough that the interval crossed no effect.",
            "claim_diffs": [
                {
                    "diff_type": "numerical_shift",
                    "preprint_claim_id": "10.1101/2024.02.20.654321::v2::abstract::0",
                    "published_claim_id": "10.1056/demo.2024.002::published::abstract::0",
                    "preprint_text": "Remote monitoring reduced 30-day readmissions by 31% after discharge for hypertensive patients.",
                    "published_text": "Remote monitoring was associated with an 8% readmission reduction, and the confidence interval crossed no effect.",
                    "change_description": "Effect size reduced from 31% to 8%, and uncertainty now crosses no effect.",
                    "numerical_delta": {
                        "metric": "readmission_reduction",
                        "preprint_value": 31.0,
                        "published_value": 8.0,
                        "absolute_delta": -23.0,
                        "relative_delta": -0.742
                    }
                }
            ],
            "materiality_score": 0.76,
            "retrieved_patterns": [
                {
                    "pattern_id": "pattern-demo-001",
                    "pattern_description": "Clinical preprints may show large effect-size reductions at publication with added hedging.",
                    "pattern_type": "effect_size_reduction",
                    "domain_tags": ["clinical-trial", "remote-monitoring"],
                    "support_count": 4,
                    "similarity_score": 0.79
                }
            ]
        }
    ]

    affected_citations = [
        {
            "affected_citation_id": "demo-drift-001::10.1038/demo.2024.100",
            "drift_event_id": "demo-drift-001",
            "citing_paper_doi": "10.1038/demo.2024.100",
            "citing_paper_title": "Antiviral treatment strategies in early respiratory infection",
            "citing_paper_authors": [{"name": "Jane Doe", "orcid": "0000-0000-0000-0000", "email": "test+jane@team-mailbox.com"}],
            "citation_context": None,
            "severity_tier": "central",
            "severity_reasoning": "The citing paper relies on the original 45% effect size as a central argument.",
            "scored_at": ts
        },
        {
            "affected_citation_id": "demo-drift-002::10.1001/demo.2024.200",
            "drift_event_id": "demo-drift-002",
            "citing_paper_doi": "10.1001/demo.2024.200",
            "citing_paper_title": "Digital follow-up programs for cardiovascular readmission prevention",
            "citing_paper_authors": [{"name": "Alex Smith", "orcid": None, "email": "test+alex@team-mailbox.com"}],
            "citation_context": None,
            "severity_tier": "comparative",
            "severity_reasoning": "The citing paper compares several interventions and uses the original 31% effect as favorable evidence.",
            "scored_at": ts
        }
    ]

    drift_patterns = [
        {
            "pattern_id": "pattern-demo-001",
            "pattern_description": "COVID-related clinical preprints may show large effect-size reductions at publication with added hedging.",
            "pattern_type": "effect_size_reduction",
            "domain_tags": ["covid-19", "clinical-trial", "remote-monitoring"],
            "source_event_ids": ["demo-drift-001", "demo-drift-002"],
            "support_count": 4,
            "created_at": ts,
            "last_updated_at": ts
        }
    ]

    notification_log = [
        {
            "affected_citation_id": "demo-drift-001::10.1038/demo.2024.100",
            "drift_event_id": "demo-drift-001",
            "recipient_email": "test+jane@team-mailbox.com",
            "subject": "Update on preprint cited in your paper: HCQ viral load study",
            "body": "Dear Dr. Doe,\\n\\nWe noticed that a preprint cited in your paper has changed in its published version. The reported viral load reduction changed from 45% to 12%, with additional hedging in the published text.\\n\\nThis is an automated informational notice for your review.",
            "status": "drafted",
            "sent_at": None,
            "error_message": None
        },
        {
            "affected_citation_id": "demo-drift-002::10.1001/demo.2024.200",
            "drift_event_id": "demo-drift-002",
            "recipient_email": "test+alex@team-mailbox.com",
            "subject": "Update on preprint cited in your paper: remote monitoring readmission study",
            "body": "Dear Dr. Smith,\\n\\nWe noticed that a preprint cited in your paper has changed in its published version. The reported readmission reduction changed from 31% to 8%, with uncertainty crossing no effect in the published text.\\n\\nThis is an automated informational notice for your review.",
            "status": "drafted",
            "sent_at": None,
            "error_message": None
        }
    ]

    write_json("preprints", preprints)
    write_json("claims", claims)
    write_json("drift_events", drift_events)
    write_json("affected_citations", affected_citations)
    write_json("drift_patterns", drift_patterns)
    write_json("notification_log", notification_log)
    print(f"Wrote demo seed data to {OUT}")


if __name__ == "__main__":
    main()
