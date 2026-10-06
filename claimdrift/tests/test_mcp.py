"""MCP integration (P0.8 / P1.6, 部署方案 T4): the server must return exactly what the local functions return, and the
citing-literature tools must be stateless (no registration; any client, any server process).
Needs the `mcp` package (agents venv). Citation tools also need Europe PMC: set CLAIMDRIFT_NET=1.
  uv run python -m unittest claimdrift.tests.test_mcp -v
"""
from __future__ import annotations

import json
import os
import unittest

try:
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

from claimdrift import provenance
from claimdrift.doctools import PaperTools
from claimdrift.documents import load_pair

CASE = "guan_nejm_clinical"
NET = os.environ.get("CLAIMDRIFT_NET") == "1"


@unittest.skipUnless(HAVE_MCP, "mcp SDK not installed")
class McpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from claimdrift.mcp_client import McpClient
        cls.client = McpClient()
        cls.pair = load_pair(CASE)
        cls.local = PaperTools(cls.pair.docs)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_tool_list(self):
        names = set(self.client.tools)
        self.assertTrue({"list_documents", "list_sections", "read_section", "search_text", "get_table", "verify_quote",
                         "get_citation_sentences", "search_in_work", "verify_citing_quote", "verify_citing_quotes",
                         "citation_evidence", "get_reference_list", "prescreen_citers", "search_more_citers",
                         "follow_citation_chain"} <= names)
        self.assertNotIn("register_citation_target", names)  # stateless (T1)
        self.assertFalse({n for n in names if "pattern" in n or "exemplar" in n})

    def test_paper_tools_identical_to_local(self):
        for name, args in [("list_sections", {"doc_id": "published"}), ("search_text", {"doc_id": "published", "query": "hazard competing"}),
                           ("read_section", {"doc_id": "published", "title": "Study Definitions"})]:
            via = self.client.call(name, {"paper_id": CASE, **args})
            self.assertEqual(via, getattr(self.local, name)(**args), name)

    def test_verify_quote_and_provenance_via_mcp(self):
        good = "We excluded incubation periods of less than 1 day because some patients had continuous exposure"
        self.assertTrue(self.client.call("verify_quote", {"paper_id": CASE, "doc_id": "published", "quote": good})["verified"])
        self.assertFalse(self.client.call("verify_quote", {"paper_id": CASE, "doc_id": "published",
                                                           "quote": "We excluded incubation periods of less than 2 days"})["verified"])
        out = {"claim_diffs": [{"evidence": [{"doc_id": "published", "quote": good}]}]}
        rows_mcp, ver = provenance.check(out, self.pair, mcp_client=self.client)
        rows_loc, _ = provenance.check(out, self.pair, paper_tools=self.local)
        self.assertEqual([(r["verified"], r["section"], r["offset"]) for r in rows_mcp], [(r["verified"], r["section"], r["offset"]) for r in rows_loc])
        self.assertEqual(ver["method"], "mcp:verify_quote")

    def test_bound_tools_hide_bound_params(self):
        view = self.client.bind({"read_section"}, paper_id=CASE)
        props = view.schemas[0]["function"]["parameters"]["properties"]
        self.assertNotIn("paper_id", props)
        self.assertIn("Study Definitions", json.loads(view.call("read_section", {"doc_id": "published", "title": "Study Definitions"}))["section"])

    def test_bound_target_fields_hidden_and_only_passed_where_declared(self):
        from claimdrift.citations.access import WORKER_TOOLS, target_ref
        ref = {"first_author": "Backer", "preprint_doi": "10.1101/x", "published_doi": "10.2807/y", "title_fragment": "t"}
        view = self.client.bind(WORKER_TOOLS, **target_ref(ref))
        for s in view.schemas:
            self.assertFalse(set(ref) & set(s["function"]["parameters"]["properties"]), s["function"]["name"])

    @unittest.skipUnless(NET, "network test (Europe PMC)")
    def test_citation_tools_identical_to_local_and_stateless(self):
        """T4: two clients (two server processes) alternate on the same work; no registration step anywhere."""
        from claimdrift.citations.access import target_ref
        from claimdrift.citations.epmc import CitationTools
        from claimdrift.citations.targets import case_targets
        from claimdrift.mcp_client import McpClient
        t = case_targets()["incubation_travellers_eurosurv"]
        ref = target_ref(t)
        local = CitationTools(t)
        with McpClient() as other:  # a freshly started server: first call works without any registration
            for wid in ("PMC7097845", "PMC7682689"):
                want = local.get_citation_sentences(wid)
                self.assertEqual(other.call("get_citation_sentences", {**ref, "work_id": wid}), want)
                self.assertEqual(self.client.call("get_citation_sentences", {**ref, "work_id": wid}), want)
                self.assertEqual(other.call("search_in_work", {**ref, "work_id": wid, "terms": "5.8"}), local.search_in_work(wid, "5.8"))
                self.assertEqual(self.client.call("search_in_work", {"work_id": wid, "terms": "5.8"}), local.search_in_work(wid, "5.8"))

    @unittest.skipUnless(NET, "network test (Europe PMC)")
    def test_mcp_access_matches_local_access(self):
        from claimdrift.citations.access import LocalCitationAccess, McpCitationAccess
        from claimdrift.citations.targets import case_targets
        t = case_targets()["incubation_travellers_eurosurv"]
        loc, rem = LocalCitationAccess(t), McpCitationAccess(t, self.client)
        self.assertEqual(rem.evidence("PMC7097845", ["5.8 days"], "x"), loc.evidence("PMC7097845", ["5.8 days"], "x"))
        items = [{"work_id": "PMC7097845", "quote": "this sentence is certainly not in the paper at all"}]
        self.assertEqual(rem.quotes_found(items), [False])


if __name__ == "__main__":
    unittest.main()
