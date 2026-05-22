from typing import Optional


def normalize_doi(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    doi = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi or None


def preprint_id(doi: str, version: str) -> str:
    return f"{normalize_doi(doi)}::{version}"


def claim_id(doi: str, version: str, section: str, claim_idx: int) -> str:
    return f"{normalize_doi(doi)}::{version}::{section}::{claim_idx}"


def affected_citation_id(drift_event_id: str, citing_doi: str) -> str:
    return f"{drift_event_id}::{normalize_doi(citing_doi)}"
