"""Every Europe PMC operation of the citation analysis behind one interface (部署方案 A4 / T1).

  LocalCitationAccess   in-process (tests, `--tools local`, and the MCP server itself, which delegates here)
  McpCitationAccess     the same calls through the MCP tool service: the agents on Agent Engine have no shared cache
                        and no bucket, so pre-screening, search_more, follow_chain and the verification evidence all run
                        on the service that owns the full-text cache

Stateless: every call carries the target's identifying fields (TARGET_KEYS); nothing is registered on the server. The
server keeps the resolved Europe PMC ids and parsed full texts in a process-level cache that only affects speed.
"""
from __future__ import annotations

import re
import urllib.parse

from . import epmc
from .prefetch import list_hits, prefetch, screen_works, spellings
from .terms import any_regex

TARGET_KEYS = ("first_author", "preprint_doi", "published_doi", "title_fragment")
WORKER_TOOLS = {"get_citation_sentences", "search_in_work"}


def target_ref(target: dict) -> dict:
    """The fields that identify a target to the citation tools (bound by the program, never chosen by the model)."""
    return {k: target.get(k) or "" for k in TARGET_KEYS}


def screening_target(ref: dict, current_claim: str, terms: list[str], quantity: dict | None = None) -> dict:
    """Minimal target dict the pre-screen needs: identity + the current claim (flag F3) + the old-value terms + what the
    value measures (citations.quantity)."""
    return {**ref, "drift": {"current_claim": current_claim or ""}, "terms": list(terms or []), "quantity": quantity or None}


class LocalCitationAccess:
    def __init__(self, target: dict, tools: epmc.CitationTools | None = None):
        self.t = target
        self.tools = tools or epmc.CitationTools(target)

    @property
    def ids(self) -> dict:
        return {k: list(v) for k, v in self.tools.ids.items()}

    # ---- candidate discovery ------------------------------------------------------------------------------------
    def prescreen(self, terms: list[str], since: str | None = None, exclude=()) -> tuple[dict, dict]:
        """Both pre-screen steps in one call (small targets, local runs)."""
        return prefetch(self.tools, self.t, terms, since=since, exclude=set(exclude or ()))

    def list_citers(self, terms: list[str], since: str | None = None, exclude=()) -> tuple[dict, dict]:
        """Pre-screen step 1 (metadata only): {work_id: hit}, stats with the truncation counts."""
        return list_hits(self.tools, terms, since, set(exclude or ()))

    def screen(self, hits: dict, terms: list[str], source: str = "prefetch") -> tuple[dict, dict]:
        """Pre-screen step 2 for one batch of hits: download the full texts, keep those that contain the old value."""
        counts: dict = {}
        return screen_works(self.tools, hits, self.t, terms, source, counts=counts), counts

    def _screen_citers(self, src: str, pid: str, terms: list[str], origin: str, since: str | None, known: set[str]) -> tuple[dict, bool]:
        q = f"CITES:{pid}_{src} AND (" + " OR ".join(f'"{t}"' for t in spellings(terms)) + ")"
        if since:
            q += f" AND FIRST_PDATE:[{since} TO 3000-12-31]"
        hits = {}
        res, total = epmc.search_all_counted(q)
        for h in res:
            w = epmc.work_of(h)
            if w["work_id"] and w["work_id"] not in known:
                hits[w["work_id"]] = {**w, "cites_target_version": []}
        return screen_works(self.tools, hits, self.t, terms, origin), len(res) < total

    def search_more(self, terms: list[str], since: str | None = None, known=()) -> tuple[dict, bool]:
        """Citing papers of the preprint and the published version that contain one of the new spellings
        -> (candidates, truncated)."""
        known, found, truncated = set(known or ()), {}, False
        for kind in ("preprint", "published"):
            if kind in self.tools.ids:
                new, cut = self._screen_citers(*self.tools.ids[kind], terms, f"search_more:{','.join(terms)}", since, known | set(found))
                found.update(new)
                truncated |= cut
        return found, truncated

    def follow_chain(self, intermediary: str, terms: list[str], since: str | None = None, known=()) -> dict:
        """Second hop: citing papers of an intermediary that relayed the old value."""
        q = f"PMCID:{intermediary.strip()}" if re.fullmatch(r"PMC\d+", intermediary.strip()) else intermediary
        res = epmc.get(epmc.EPMC + "search?" + urllib.parse.urlencode({"query": q, "format": "json", "resultType": "lite", "pageSize": 1}))
        res = res.get("resultList", {}).get("result", [])
        if not res:
            return {"error": f"intermediary not found: {intermediary}"}
        h = res[0]
        found, cut = self._screen_citers(h["source"], h["id"], terms, f"chain:{(h.get('pmcid') or h.get('id'))}", since, set(known or ()))
        return {"intermediary": (h.get("title") or "")[:120], "cands": found, "truncated": cut}

    # ---- reading -----------------------------------------------------------------------------------------------
    def evidence(self, work_id: str, terms: list[str], sentence: str = "") -> dict:
        """What the verifier sees for one 'superseded' verdict (orchestra P1.2)."""
        cit = self.tools.get_citation_sentences(work_id)
        pat = any_regex(terms)
        txt = self.tools.full_text(work_id) or ""
        return {"work_id": work_id, "reference_matched": cit.get("reference_matched"),
                "sentences_citing_target": [s["with_neighbours"] for s in cit.get("sentences", [])][:4],
                "sentences_with_old_value": [s[:600] for s in epmc.sentences(txt) if pat.search(s)][:4],
                "worker_sentence_verbatim": self.tools.quote_found(work_id, sentence or "")}

    def quote_found(self, work_id: str, quote: str) -> bool:
        return self.tools.quote_found(work_id, quote or "")

    def quotes_found(self, items: list[dict]) -> list[bool]:
        return [self.quote_found(str(i.get("work_id") or ""), str(i.get("quote") or "")) for i in items]

    def worker_tools(self):
        from .orchestra import LocalWorkerTools
        return LocalWorkerTools(self.tools)


class McpCitationAccess:
    """Same interface, through the MCP tool service (`client` = mcp_client.McpClient)."""

    def __init__(self, target: dict, client):
        self.t, self.client = target, client
        self.ref = target_ref(target)
        self.current = (target.get("drift") or {}).get("current_claim") or ""

    @property
    def quantity(self) -> dict:
        return self.t.get("quantity") or {}  # set by the Orchestra before the pre-screen

    def _call(self, name: str, args: dict):
        out = self.client.call(name, {**self.ref, **args})
        if isinstance(out, dict) and "error" in out and len(out) == 1:
            raise RuntimeError(f"MCP {name}: {out['error']}")
        return out

    def prescreen(self, terms: list[str], since: str | None = None, exclude=()) -> tuple[dict, dict]:
        r = self._call("prescreen_citers", {"current_claim": self.current, "quantity": self.quantity, "terms": list(terms), "since": since or "",
                                            "exclude": sorted(exclude or ())})
        return r["candidates"], r["stats"]

    def list_citers(self, terms: list[str], since: str | None = None, exclude=()) -> tuple[dict, dict]:
        r = self._call("list_citers", {"terms": list(terms), "since": since or "", "exclude": sorted(exclude or ())})
        return r["hits"], r["stats"]

    def screen(self, hits: dict, terms: list[str], source: str = "prefetch") -> tuple[dict, dict]:
        r = self._call("screen_citers", {"hits": hits, "terms": list(terms), "current_claim": self.current, "quantity": self.quantity, "source": source})
        return r["candidates"], r["counts"]

    def search_more(self, terms: list[str], since: str | None = None, known=()) -> tuple[dict, bool]:
        r = self._call("search_more_citers", {"current_claim": self.current, "quantity": self.quantity, "terms": list(terms), "since": since or "",
                                              "known": sorted(known or ())})
        return r["candidates"], bool(r.get("truncated"))

    def follow_chain(self, intermediary: str, terms: list[str], since: str | None = None, known=()) -> dict:
        r = self.client.call("follow_citation_chain", {**self.ref, "current_claim": self.current, "quantity": self.quantity, "intermediary": intermediary,
                                                       "terms": list(terms), "since": since or "", "known": sorted(known or ())})
        return r if "error" in r else {"intermediary": r["intermediary"], "cands": r["candidates"], "truncated": bool(r.get("truncated"))}

    def evidence(self, work_id: str, terms: list[str], sentence: str = "") -> dict:
        return self._call("citation_evidence", {"work_id": work_id, "terms": list(terms), "sentence": sentence or ""})

    def quote_found(self, work_id: str, quote: str) -> bool:
        return bool(self._call("verify_citing_quote", {"work_id": work_id, "quote": quote or ""}).get("verified"))

    def quotes_found(self, items: list[dict]) -> list[bool]:
        if not items:
            return []
        return [bool(x) for x in self._call("verify_citing_quotes", {"items": items})["verified"]]

    def worker_tools(self):
        return self.client.bind(WORKER_TOOLS, **self.ref)
