"""事后校验 (§3.4, P0.4): every evidence quote must be found verbatim in the document it names.

Runs through the MCP `verify_quote` tool (P0.8) -- the same perception layer the agents use -- and never through an
LLM: the point is that anyone can audit the analyzer's evidence by string matching. Output goes to
drift_events.provenance (one row per quote) and drift_events.verification (event-level status):
  verified     all quotes found
  partial      some found
  unsupported  none found, or no quotes at all
"""
from __future__ import annotations

import datetime as dt


def _verify_local(paper_tools):
    def f(doc_id: str, quote: str) -> dict:
        return paper_tools.verify(doc_id, quote)
    return f


def _verify_mcp(client, paper_id: str):
    def f(doc_id: str, quote: str) -> dict:
        return client.call("verify_quote", {"paper_id": paper_id, "doc_id": doc_id, "quote": quote})
    return f


def check(output: dict, pair, mcp_client=None, paper_tools=None) -> tuple[list[dict], dict]:
    """-> (provenance rows, verification summary). Uses MCP when a client is given, else the local tools (tests)."""
    if mcp_client is not None:
        verify = _verify_mcp(mcp_client, pair.paper_id)
        via = "mcp:verify_quote"
    else:
        verify = _verify_local(paper_tools)
        via = "local:quote_found"
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = []
    for i, d in enumerate((output or {}).get("claim_diffs") or []):
        for e in d.get("evidence") or []:
            doc_id = str(e.get("doc_id") or "")
            quote = str(e.get("quote") or "")
            res = verify(doc_id, quote) if doc_id and quote else {"verified": False}
            is_pre = doc_id.startswith("preprint")
            rows.append({"claim_diff_idx": i, "doc_id": doc_id,
                         "doi": pair.preprint_doi if is_pre else pair.published_doi,
                         "version": doc_id.replace("preprint_", "") if is_pre else "published",
                         "section": res.get("section") or e.get("section"), "section_claimed": e.get("section"),
                         "quote": quote, "offset": res.get("offset"), "tool": via, "tool_call_idx": None,
                         "text_source": res.get("text_source") or "jats", "verified": bool(res.get("verified")), "verified_at": now})
    n_ok = sum(r["verified"] for r in rows)
    status = "verified" if rows and n_ok == len(rows) else ("partial" if n_ok else "unsupported")
    return rows, {"quotes_total": len(rows), "quotes_verified": n_ok, "status": status, "verified_at": now, "method": via}
