"""ClaimDrift MCP server -- the perception layer (§3.6, P0.8). Replaces the Elastic Agent Builder MCP endpoint. Agents
reason; they reach paper text and citing literature only through these tools.

  paper full text    list_documents, list_sections, read_section, search_text, get_table   (paper_id = case-bank slug
                                                                                            or docstore directory)
  verification       verify_quote          (事后校验: verbatim check + section/offset/text_source)
  citing literature  get_citation_sentences, search_in_work, verify_citing_quote(s), citation_evidence,
                     get_reference_list                                    (Europe PMC open full text)
  candidate search   list_citers + screen_citers (pre-screen in batches), prescreen_citers (both at once),
                     search_more_citers, follow_citation_chain

Stateless (部署方案 T1/T2): every citing-literature tool carries the target's fields (first_author, preprint_doi,
published_doi, title_fragment); there is no registration step, so any instance can answer any call. Resolved Europe PMC
ids and parsed full texts sit in a process-level cache that only affects speed. The on-disk full-text cache
(CLAIMDRIFT_EPMC_CACHE) is written only by this service.

Not exposed: search_semantic (ELSER over citing sentences) -- P2, undecided. drift_pattern tools -- removed (P1.9).
Consumers: the drift pipeline / supervisor (verify_quote after analysis), the citation analysis (workers read citing
papers; the orchestrator's searches), and the BFF (author self-check of an already published paper). Locally each client
starts the server over stdio; deployed, the server runs as its own Cloud Run service over streamable HTTP and the
clients point CLAIMDRIFT_MCP_URL at it.

Run: python -m claimdrift.mcp_server                       (stdio)
     python -m claimdrift.mcp_server --http --port 8090    (stateless streamable HTTP, path /mcp)
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from mcp.server.fastmcp import FastMCP

from .doctools import PaperTools
from .documents import load_pair

mcp = FastMCP("claimdrift")

_papers: dict[str, PaperTools] = {}
_targets: OrderedDict[tuple, object] = OrderedDict()
_targets_lock = threading.Lock()
MAX_CACHED_TARGETS = 16


def _paper(paper_id: str) -> PaperTools:
    if paper_id not in _papers:
        _papers[paper_id] = PaperTools(load_pair(paper_id).docs)
    return _papers[paper_id]


def _access(first_author: str, preprint_doi: str, published_doi: str, title_fragment: str = "", current_claim: str = "",
            terms: list[str] | None = None, quantity: dict | None = None):
    """LocalCitationAccess for one target. The CitationTools behind it (Europe PMC ids + parsed full texts) are cached
    per target; the screening fields (current claim, terms, quantity) are per call."""
    from .citations.access import LocalCitationAccess, screening_target
    from .citations.epmc import CitationTools
    ref = {"first_author": first_author or "", "preprint_doi": (preprint_doi or "").lower(),
           "published_doi": (published_doi or "").lower(), "title_fragment": title_fragment or ""}
    key = tuple(ref.values())
    with _targets_lock:
        tools = _targets.get(key)
        if tools is not None:
            _targets.move_to_end(key)
    if tools is None:
        tools = CitationTools(ref)  # network: resolves the Europe PMC ids of both versions
        with _targets_lock:
            _targets[key] = tools
            while len(_targets) > MAX_CACHED_TARGETS:
                _targets.popitem(last=False)
    return LocalCitationAccess(screening_target(ref, current_claim, terms or [], quantity), tools)


# ---------------------------------------------------------------- paper full text
@mcp.tool()
def list_documents(paper_id: str) -> dict:
    """List the documents of a paper (every preprint version and the published version) with sizes and text source."""
    return _paper(paper_id).list_documents()


@mcp.tool()
def list_sections(paper_id: str, doc_id: str) -> dict:
    """List section titles and tables of one document (doc_id: preprint_v1, preprint_v2, ..., published)."""
    return _paper(paper_id).list_sections(doc_id)


@mcp.tool()
def read_section(paper_id: str, doc_id: str, title: str) -> dict:
    """Read the full text of one section (fuzzy title match, e.g. 'Methods > Study Definitions' or 'Statistical analysis')."""
    return _paper(paper_id).read_section(doc_id, title)


@mcp.tool()
def search_text(paper_id: str, doc_id: str, query: str) -> dict:
    """Keyword/number search inside one document. total_hits == 0 is evidence that something is ABSENT from that document."""
    return _paper(paper_id).search_text(doc_id, query)


@mcp.tool()
def get_table(paper_id: str, doc_id: str, index: int) -> dict:
    """Return one table (flattened rows) by index from list_sections."""
    return _paper(paper_id).get_table(doc_id, index)


@mcp.tool()
def verify_quote(paper_id: str, doc_id: str, quote: str) -> dict:
    """Check that a quote occurs verbatim (case/punctuation-insensitive) in the named document; returns the section and
    offset where it was found and the document's text source."""
    return _paper(paper_id).verify(doc_id, quote)


# ---------------------------------------------------------------- citing literature (worker tools)
@mcp.tool()
def get_citation_sentences(work_id: str, first_author: str, preprint_doi: str, published_doi: str,
                           title_fragment: str = "") -> dict:
    """Sentences of one citing paper in which the target is cited (matched through its reference list), each with its
    neighbouring sentences; reference_matched tells whether the reference list contains the target at all."""
    return _access(first_author, preprint_doi, published_doi, title_fragment).tools.get_citation_sentences(work_id)


@mcp.tool()
def search_in_work(work_id: str, terms: str, first_author: str = "", preprint_doi: str = "", published_doi: str = "",
                   title_fragment: str = "") -> dict:
    """Sentences of one citing paper containing ALL comma-separated terms (e.g. to see whether a value is a model input)."""
    return _access(first_author, preprint_doi, published_doi, title_fragment).tools.search_in_work(work_id, terms)


@mcp.tool()
def verify_citing_quote(work_id: str, quote: str, first_author: str = "", preprint_doi: str = "", published_doi: str = "",
                        title_fragment: str = "") -> dict:
    """Check that a sentence attributed to a citing paper occurs verbatim in its full text."""
    return {"work_id": work_id, "verified": _access(first_author, preprint_doi, published_doi, title_fragment).quote_found(work_id, quote)}


@mcp.tool()
def verify_citing_quotes(items: list[dict], first_author: str = "", preprint_doi: str = "", published_doi: str = "",
                         title_fragment: str = "") -> dict:
    """Batch form of verify_citing_quote: items = [{"work_id", "quote"}] -> {"verified": [bool, ...]} in the same order."""
    return {"verified": _access(first_author, preprint_doi, published_doi, title_fragment).quotes_found(items)}


@mcp.tool()
def citation_evidence(work_id: str, terms: list[str], first_author: str, preprint_doi: str, published_doi: str,
                      title_fragment: str = "", sentence: str = "") -> dict:
    """Evidence for re-checking a 'superseded' verdict: whether the reference list matches the target, the sentences
    citing it, the sentences holding the old value, and whether the worker's sentence is verbatim."""
    return _access(first_author, preprint_doi, published_doi, title_fragment).evidence(work_id, terms, sentence)


@mcp.tool()
def get_reference_list(work_id: str) -> dict:
    """Lower-cased plain text of a paper's reference list (empty when there is no open full text)."""
    from .citations.epmc import CitationTools
    from .selfcheck import ref_list_text
    xml = CitationTools({}).fetch(work_id)
    return {"work_id": work_id, "full_text": xml is not None, "references": ref_list_text(xml) if xml else ""}


# ---------------------------------------------------------------- candidate search (orchestrator side)
@mcp.tool()
def prescreen_citers(first_author: str, preprint_doi: str, published_doi: str, terms: list[str], title_fragment: str = "",
                     current_claim: str = "", since: str = "", exclude: list[str] | None = None,
                     quantity: dict | None = None) -> dict:
    """Program pre-screen: citing papers (of either version) whose open full text contains the old value, with the
    deterministic flags F1/F2/F3. Downloads and caches the full texts."""
    cands, stats = _access(first_author, preprint_doi, published_doi, title_fragment, current_claim, terms, quantity).prescreen(
        terms, since or None, exclude or ())
    return {"candidates": cands, "stats": stats}


@mcp.tool()
def list_citers(first_author: str, preprint_doi: str, published_doi: str, terms: list[str], title_fragment: str = "",
                since: str = "", exclude: list[str] | None = None) -> dict:
    """Pre-screen step 1 (metadata only, fast): citing papers of either version whose open full text Europe PMC finds
    the old value in, plus the counts that show any cut-off (hit_count vs fetched, papers without open full text)."""
    hits, stats = _access(first_author, preprint_doi, published_doi, title_fragment, "", terms).list_citers(
        terms, since or None, exclude or ())
    return {"hits": hits, "stats": stats}


@mcp.tool()
def screen_citers(first_author: str, preprint_doi: str, published_doi: str, hits: dict, terms: list[str],
                  title_fragment: str = "", current_claim: str = "", source: str = "prefetch",
                  quantity: dict | None = None) -> dict:
    """Pre-screen step 2 for one batch of list_citers hits: download and cache the full texts, keep the papers whose
    text contains the old value, with the deterministic flags F1/F2/F3."""
    cands, counts = _access(first_author, preprint_doi, published_doi, title_fragment, current_claim, terms, quantity).screen(
        hits, terms, source)
    return {"candidates": cands, "counts": counts}


@mcp.tool()
def search_more_citers(first_author: str, preprint_doi: str, published_doi: str, terms: list[str], title_fragment: str = "",
                       current_claim: str = "", since: str = "", known: list[str] | None = None,
                       quantity: dict | None = None) -> dict:
    """Pre-screen the citing papers again with alternative spellings of the old value; `known` work_ids are skipped."""
    a = _access(first_author, preprint_doi, published_doi, title_fragment, current_claim, terms, quantity)
    cands, truncated = a.search_more(terms, since or None, known or ())
    return {"candidates": cands, "truncated": truncated}


@mcp.tool()
def follow_citation_chain(first_author: str, preprint_doi: str, published_doi: str, intermediary: str, terms: list[str],
                          title_fragment: str = "", current_claim: str = "", since: str = "",
                          known: list[str] | None = None, quantity: dict | None = None) -> dict:
    """Second hop: pre-screen the citing papers of an intermediary (PMC id, or 'Author Year' + title words) for the old
    value; `known` work_ids are skipped."""
    a = _access(first_author, preprint_doi, published_doi, title_fragment, current_claim, terms, quantity)
    r = a.follow_chain(intermediary, terms, since or None, known or ())
    return r if "error" in r else {"intermediary": r["intermediary"], "candidates": r["cands"], "truncated": r["truncated"]}


def main() -> None:
    import argparse
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve over stateless streamable HTTP instead of stdio")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8090")))
    a = ap.parse_args()
    if a.http:
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.host, mcp.settings.port = a.host, a.port
        # Stateless: no session affinity needed, any Cloud Run instance answers any request (T2). JSON responses: one
        # request -> one response, no SSE stream to hold open through the Google Frontend.
        mcp.settings.stateless_http, mcp.settings.json_response = True, True
        # Behind Cloud Run the Host header is the service URL; access control is Cloud Run IAM (ID token), not DNS
        # rebinding protection (which only allows localhost).
        mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
