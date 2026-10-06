"""Offline tests (no LLM, no network, no ES). Run from the repo root:
  uv run python -m unittest discover -s claimdrift/tests -v
"""
from __future__ import annotations

import json
import unittest

from claimdrift import config, drift_analyzer, notifier, provenance, review, store
from claimdrift.citations import terms
from claimdrift.citations.orchestra import Orchestra
from claimdrift.citations.targets import build_targets
from claimdrift.doctools import PaperTools
from claimdrift.documents import load_pair
from claimdrift.selfcheck import old_or_new

CASE = "incubation_travellers_eurosurv"


class FakeBackend:
    """Returns canned replies in order; records the messages it was sent."""

    def __init__(self, replies):
        self.replies, self.sent = list(replies), []
        self.calls = self.prompt_tokens = self.output_tokens = 0
        self.model = "fake"

    def chat(self, messages, tools=None):
        self.sent.append(messages)
        self.calls += 1
        text = self.replies.pop(0)
        return {"content": text, "tool_calls": [], "assistant_message": {"role": "assistant", "content": text}}

    def usage(self):
        return {"model": self.model, "calls": self.calls, "prompt_tokens": 0, "output_tokens": 0}


class TermsTest(unittest.TestCase):
    def test_spellings_of_a_day_value(self):
        rx = terms.term_regex("5.8 days")
        for s in ("mean 5.8 days", "5·8 days", "5.80 days (Backer et al.)", "a 5.8-day incubation", "5.8 d", "5.8days"):
            self.assertTrue(rx.search(s), s)
        for s in ("15.8 days", "5.85 days", "5.8 weeks", "25.8 days"):
            self.assertFalse(rx.search(s), s)
        v = terms.query_variants("5.8 days")
        for want in ("5.8 days", "5·8 days", "5.80 days", "5.8-day"):
            self.assertIn(want, v)

    def test_percent_and_plain_numbers(self):
        rx = terms.term_regex("50.7%")
        self.assertTrue(rx.search("75 patients (50.7 %) had"))
        self.assertTrue(rx.search("50·7%"))
        self.assertFalse(rx.search("150.7%"))
        self.assertTrue(terms.term_regex("5.47").search("ranges up to 5·47 (95% CI"))
        self.assertTrue(terms.term_regex("29,500").search("about 29,500 cases"))
        self.assertTrue(terms.term_regex("0 to 24").search("range, 0 to 24 days"))

    def test_old_value_terms_from_real_claims(self):
        """Production derivation of search terms: ranges stay whole, interval estimates, years and CI levels are not values."""
        ovt = terms.old_value_terms
        self.assertEqual(ovt("We estimated that the mean R 0 ranges from 3.30 (95%CI: 2.73-3.96) to 5.47 (95%CI: 4.16-7.10)",
                             "We estimated that the mean R 0 ranges from 2.24 to 3.58"), ["3.30 to 5.47"])
        self.assertEqual(ovt("R fluctuated between 1.6–2.9 from mid-December to mid-January 2020",
                             "R t declined from 2.35 one week before travel restrictions to 1.05"), ["1.6 to 2.9"])
        self.assertEqual(ovt("The median incubation period was 3.0 days (range, 0 to 24.0 days).",
                             "The median incubation period was 4 days (interquartile range, 2 to 7)."), ["0 to 24.0 days", "3.0 days"])
        self.assertEqual(ovt("mean incubation period 5.8 (4.6 - 7.9, 95% CI) days", "6.4 days (95% CrI 5.6-7.7)"), ["5.8 days"])
        self.assertEqual(ovt("Our results show that 2019-nCoV has substantial potential (OR 3.1717, 95% CI 1.3-7.7)", ""), ["3.1717"])

    def test_range_spellings(self):
        rx = terms.term_regex("0 to 24")
        for s_ in ("range, 0 to 24 days", "Range (0-24)", "0–24 days", "between 0 and 24 days", "0 to 24.0 days", "0 − 24", "0- to 24-day"):
            self.assertTrue(rx.search(s_), s_)
        for s_ in ("10-24 days", "0 to 245", "0 to 24.5 days", "0 to 2 days", "24 days"):
            self.assertFalse(rx.search(s_), s_)
        v = terms.query_variants("0 to 24")
        for want in ("0-24", "0–24", "0 and 24", "0 to 24.0"):
            self.assertIn(want, v)
        v = terms.query_variants("3.30 to 5.47")  # trailing zeros normalised like the local regex
        for want in ("3.3 to 5.47", "3.3–5.47", "3.30–5.47", "3.3 and 5.47"):
            self.assertIn(want, v)
        self.assertIn("3 days", terms.query_variants("3.0 days"))

    def test_regex_matches_every_query_spelling(self):
        """The Europe PMC query and the local screen use the same rules: no spelling is fetched and then dropped."""
        for t in ("5.8 days", "50.7%", "5.47", "0 to 24", "0 to 24.0 days", "29,500"):
            rx = terms.term_regex(t)
            for v in terms.query_variants(t):
                self.assertTrue(rx.search(v), (t, v))

    def test_quantity_match_value_unit_property(self):
        """Value + unit + measured property: a unit next to the value must be the target's; a value without a unit
        counts only next to the measured property."""
        q = {"property_terms": ["incubation period", "incubation"], "unit": "days"}
        sents = ["Guan et al. reported a median incubation period of 3.0 days (range, 0–24.0), shorter than SARS.",  # no unit, property
                 "Total CT severity scores (range, 0–24) were summed over six lung zones.",                     # no unit, no property
                 "Cytokine mRNA was measured over a 0–24 h period.",                                            # other unit
                 "The incubation period ranged widely.", "It spanned 0 to 24 days in one cohort.",              # property in previous sentence
                 "Ages 0–24 years accounted for 5% of cases."]
        self.assertEqual(terms.quantity_sentences(sents, ["0 to 24"], q), [0, 4])
        # without the quantity step (model call failed): a term without a unit keeps the value-only rule
        self.assertEqual(terms.quantity_sentences(sents, ["0 to 24"], None), [0, 1, 2, 4, 5])
        # a term written with its unit: a bare value needs the property, another unit is dropped
        q58 = {"property_terms": ["incubation"], "unit": "days"}
        s58 = ["Incubation was set to 5.8 (Backer et al.).", "The R0 was 5.8 in Wuhan.", "a 5.8-day incubation", "5.8 h later",
               "median incubation 5.1 days (95% CI 4.5-5.8)", "incubation 5.8–7.0 days", "incubation 5.8 to 7 days"]
        self.assertEqual(terms.quantity_sentences(s58, ["5.8 days"], q58), [0, 2])
        self.assertEqual(terms.quantity_sentences(s58, ["5.8 days"], None), [2])
        self.assertEqual(terms.value_spellings("5.8 days")[:2], ["5.8", "5·8"])
        self.assertEqual(terms.value_term("0 to 24.0 days"), "0 to 24.0")

    def test_subscript_property(self):
        q = {"property_terms": ["r0"], "unit": "none"}
        self.assertEqual(terms.quantity_sentences(["the true R 0 lies between 3.3 and 5.47"], ["3.30 to 5.47"], q), [0])

    def test_proposed_spellings_count_when_the_quantity_is_the_old_value(self):
        q = {"property_terms": ["incubation", "liver dysfunction", "r0"], "unit": None,
             "proposed": ["24 days", "75 out of 148", "3.3"]}
        old = ["0 to 24.0 days", "50.7%", "3.30 to 5.47"]
        sents = ["The incubation period may be as long as 24 days.",      # 24 days is not the range 0-24
                 "75 out of 148 patients had liver dysfunction.",          # 75/148 = 50.7%
                 "75 out of 148 respondents agreed.",                       # no measured property
                 "The R 0 was 3.3 in Wuhan.",                              # 3.3 alone is not the range 3.30-5.47
                 "the true R0 lies between 3.3 and 5.47"]                  # the whole range (also the old term itself)
        self.assertEqual(terms.quantity_sentences(sents, old + q["proposed"], q), [1, 4])
        q2 = {"property_terms": ["liver dysfunction"], "unit": None, "proposed": ["half"]}
        self.assertEqual(terms.quantity_sentences(["half of the patients had liver dysfunction"], ["50.7%", "half"], q2), [])
        self.assertEqual(terms.value_term("75 out of 148"), "75 out of 148")
        self.assertEqual(terms.numbers("29,500 cases"), ["29500"])

    def test_table_rows_carry_headers(self):
        from claimdrift.citations import epmc
        xml = ("<table-wrap><label>Table 2</label><caption><title>Liver injury in COVID-19</title></caption><table>"
               "<thead><tr><th>Study</th><th>N</th><th>Abnormal liver function, n (%)</th></tr></thead>"
               "<tbody><tr><td>Fan et al [33]</td><td>148</td><td>75 (50.7)</td></tr><tr><td>Cai</td><td>298</td><td>44</td></tr>"
               "</tbody></table></table-wrap>")
        rows = epmc.table_rows(xml)
        self.assertEqual(rows[0][0], "Fan et al [33] | 148 | 75 (50.7)")
        self.assertIn("Abnormal liver function", rows[0][1])
        q = {"property_terms": ["abnormal liver function"], "unit": "%"}
        self.assertEqual(terms.quantity_sentences([r for r, _ in rows], ["50.7%"], q, contexts=[c for _, c in rows]), [0])
        self.assertEqual(terms.quantity_sentences([r for r, _ in rows], ["50.7%"], q), [])

    def test_quantity_context_parsing(self):
        from claimdrift.citations.quantity import quantity_context
        t = {"terms": ["0 to 24"], "drift": {"preprint_v1_claim": "median incubation period 3.0 days (range 0 to 24.0 days)"}}
        fb = FakeBackend([json.dumps({"property_terms": ["Incubation period", "incubation"], "unit": "day"})])
        self.assertEqual(quantity_context(t, fb), {"property_terms": ["incubation period", "incubation"], "unit": "days"})
        self.assertIsNone(quantity_context(t, FakeBackend(["no json"])))

    def test_old_value_terms(self):
        self.assertEqual(terms.old_value_terms("mean incubation period 5.8 days (95% CI 4.6-7.9) from 34 cases",
                                               "mean incubation period 6.4 days (95% CI 5.6-7.7) from 88 cases")[0], "5.8 days")
        self.assertEqual(terms.old_value_terms("75 patients (50.7%) showed abnormal liver function", "55 patients (37.2%)"), ["50.7%"])
        self.assertEqual(terms.old_value_terms("the effect was robust", "the effect was weaker"), [])
        self.assertEqual(terms.old_value_terms("value 2.5", "value 2.5 confirmed"), [])


class ReviewTest(unittest.TestCase):
    def test_severity_mismatch_rule(self):
        self.assertTrue(review.severity_mismatch("no_change", "significant"))
        self.assertTrue(review.severity_mismatch("major", "minor"))
        self.assertFalse(review.severity_mismatch("minor", "medium"))
        self.assertFalse(review.severity_mismatch(None, "major"))

    def test_event_triggers_and_human_decision_kept(self):
        ev = {"verification": {"quotes_total": 3, "quotes_verified": 2}, "abstract_severity": {"class": "no_change"},
              "fulltext_severity": {"tier": "major"}, "preprint_withdrawn_versions": ["v3"], "analysis_route": {"route": "stuffed"}}
        review.apply_event(ev)
        self.assertEqual(ev["review_status"], "pending")
        self.assertEqual(set(ev["review_reasons"]), {"quote_unverified", "severity_mismatch", "withdrawn_version"})
        ok = {"verification": {"quotes_total": 2, "quotes_verified": 2}, "analysis_route": {"route": "stuffed"}}
        self.assertEqual(review.apply_event(ok)["review_status"], "not_required")
        decided = {"review_status": "approved", "verification": {"quotes_total": 1, "quotes_verified": 0}}
        self.assertEqual(review.apply_event(decided)["review_status"], "approved")

    def test_citation_status(self):
        self.assertEqual(review.citation_status("superseded"), ("not_required", []))  # no review before notifying
        self.assertEqual(review.citation_status("unclear"), ("pending", ["unclear"]))
        self.assertEqual(review.citation_status("current")[0], "not_required")


class HumanCorrectionTest(unittest.TestCase):
    def test_event_correction_keeps_machine_values(self):
        ev = {"abstract_severity": {"class": "major"}, "fulltext_severity": {"tier": "minor"},
              "claim_diffs": [{"root_cause": "reporting_choice", "severity_tier": "minor"}]}
        review.decide(ev, "approved", None, "abstract was incomplete in v1",
                      {"abstract_class": "minor", "claim_diffs": [{"idx": 0, "severity_tier": "medium"}]}, kind="event")
        self.assertEqual(ev["abstract_severity"]["class"], "minor")
        self.assertEqual(ev["claim_diffs"][0]["severity_tier"], "medium")
        self.assertEqual(ev["machine_verdict"], {"abstract_class": "major", "claim_diffs[0].severity_tier": "minor"})
        self.assertEqual(ev["corrected_fields"], ["abstract_class", "claim_diffs[0].severity_tier"])
        self.assertIsNone(ev["reviewer"])  # reviewer is optional
        review.decide(ev, "approved", "jz", "", {"abstract_class": "no_change"}, kind="event")
        self.assertEqual(ev["machine_verdict"]["abstract_class"], "major")  # first machine value is kept

    def test_correction_validation(self):
        ev = {"claim_diffs": [{"root_cause": "data_revision"}]}
        for bad in ({"fulltext_tier": "huge"}, {"claim_diffs": [{"idx": 3, "root_cause": "data_revision"}]},
                    {"claim_diffs": [{"idx": 0, "root_cause": "made_up"}]}, {"cites": "superseded"}):
            with self.assertRaises(ValueError):
                review.decide(dict(ev), "approved", None, "", bad, kind="event")
        with self.assertRaises(ValueError):
            review.decide(dict(ev), "rejected", None, "", {"fulltext_tier": "minor"}, kind="event")

    def test_citation_correction_updates_notification_flag(self):
        ac = {"cites": "unclear", "role": "unknown", "needs_notification": False}
        review.decide(ac, "approved", "", "", {"cites": "superseded", "role": "reported_as_fact"})
        self.assertTrue(ac["needs_notification"])
        self.assertEqual(ac["machine_verdict"], {"cites": "unclear", "role": "unknown"})
        notifier.check_gate(ac, {"review_status": "not_required"})  # approved without a reviewer name is enough


class NotifierGateTest(unittest.TestCase):
    def test_gate(self):
        ev = {"review_status": "not_required", "claim_diffs": [{"preprint_text": "5.8 days", "published_text": "6.4 days"}]}
        ac = {"cites": "superseded", "review_status": "not_required", "reviewer": None, "claim_diff_idx": 0}
        notifier.check_gate(ac, ev)  # no approval needed
        with self.assertRaises(notifier.GateError):
            notifier.check_gate(ac | {"review_status": "rejected"}, ev)
        with self.assertRaises(notifier.GateError):
            notifier.check_gate(ac | {"cites": "current"}, ev)
        with self.assertRaises(notifier.GateError):
            notifier.check_gate(ac, ev | {"review_status": "rejected"})
        subject, body = notifier.draft(ac | {"sentence": "We used 5.8 days.", "work_id": "PMC1"}, ev)
        self.assertIn("5.8 days", body)
        self.assertIn("6.4 days", body)

    def test_recipient_is_always_the_test_inbox(self):
        self.assertEqual(config.NOTIFY_OVERRIDE_EMAIL, "claimdriftnotifier@gmail.com")


class CaseBankTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pair = load_pair(CASE)

    def test_pair_is_v1_vs_published(self):
        p = self.pair
        self.assertEqual(p.pre_id, "preprint_v1")
        self.assertEqual(p.preprint_doi, "10.1101/2020.01.27.20018986")
        self.assertTrue(p.published_doi.startswith("10.2807/"))
        self.assertEqual(p.preprint.text_source, "jats")
        self.assertLess(p.est_tokens(), config.STUFF_LIMIT_TOKENS)

    def test_provenance_local(self):
        pt = PaperTools(self.pair.docs)
        real = self.pair.published.abstract[:120]
        out = {"claim_diffs": [{"evidence": [{"doc_id": "published", "quote": real, "section": "Abstract"},
                                             {"doc_id": "published", "quote": "the mean incubation period was 42 days in every case", "section": "Results"}]}]}
        rows, ver = provenance.check(out, self.pair, paper_tools=pt)
        self.assertEqual([r["verified"] for r in rows], [True, False])
        self.assertEqual(rows[0]["section"], "Abstract")
        self.assertEqual(ver["status"], "partial")

    def test_stuffed_route_single_call_and_normalize(self):
        reply = json.dumps({"drift_summary": "s", "claim_diffs": [
            {"diff_type": "numerical_shift", "preprint_text": "5.8 days", "published_text": "6.4 days", "change_description": "c",
             "root_cause": "data_revision", "materiality": 0.2, "evidence": [{"doc_id": "published", "quote": "q"}], "rationale": "r",
             "introduced_between": "extra key the mapping does not know"},
            {"diff_type": "weird", "root_cause": "made_up", "materiality": "0.95", "evidence": []}], "materiality_score": 0.95})
        fb = FakeBackend([reply])
        res = drift_analyzer.analyze(self.pair, backend=fb, force_route="stuffed")
        self.assertEqual(fb.calls, 1)
        self.assertEqual(res["route"]["route"], "stuffed")
        self.assertIn("FULL TEXT", fb.sent[0][1]["content"])
        out = res["output"]
        self.assertEqual([d["severity_tier"] for d in out["claim_diffs"]], ["minor", "major"])
        self.assertEqual(out["fulltext_tier"], "major")
        self.assertIn("label_problems", out["claim_diffs"][1])
        ev = store.drift_event_doc(self.pair, res, {"class": "minor"}, [], {"quotes_total": 0, "quotes_verified": 0, "status": "unsupported"})
        self.assertNotIn("introduced_between", ev["claim_diffs"][0])
        self.assertEqual(ev["preprint_version_compared"], "v1")
        self.assertEqual(ev["event_id"], store.event_id_for(self.pair.preprint_doi, self.pair.published_doi))

    def test_claims_route_uses_extractor_then_one_call(self):
        extract_reply = json.dumps({"claims": [{"section": "Results", "text": "The mean incubation period was 6.4 days.", "kind": "finding",
                                                "numbers": [], "support_sections": ["Methods"]}]})
        made = []

        def factory():
            b = FakeBackend([extract_reply] * 50)
            made.append(b)
            return b
        fb = FakeBackend([json.dumps({"drift_summary": "s", "claim_diffs": [], "materiality_score": 0.1})])
        res = drift_analyzer.analyze(self.pair, backend=fb, force_route="claims", extractor_factory=factory)
        self.assertEqual(res["route"]["route"], "claims")
        self.assertEqual(fb.calls, 1)
        self.assertIn("CLAIMS", fb.sent[0][1]["content"])
        self.assertNotIn("FULL TEXT", fb.sent[0][1]["content"])
        self.assertTrue(made and set(res["claims"]) == {"preprint_v1", "published"})
        self.assertTrue(res["claims"]["published"][0]["id"].startswith("pub"))

    def test_case_bank_target(self):
        ev = {"event_id": "E", "preprint_doi": self.pair.preprint_doi, "published_doi": self.pair.published_doi,
              "claim_diffs": [{"preprint_text": "34 cases", "root_cause": "data_revision"},
                              {"preprint_text": "mean incubation period 5.8 days", "root_cause": "data_revision"}]}
        t = build_targets(ev, CASE)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["claim_diff_idx"], 1)
        self.assertEqual(t[0]["terms"], ["5.8 days"])

    def test_derived_targets_skip_reporting_choice(self):
        ev = {"event_id": "E", "claim_diffs": [{"preprint_text": "R0 of 5.47", "published_text": "R0 of 3.58", "root_cause": "reporting_choice"},
                                               {"preprint_text": "CFR 2.1%", "published_text": "CFR 1.4%", "root_cause": "data_revision"}]}
        t = build_targets(ev, "not_a_case")
        self.assertEqual([x["claim_diff_idx"] for x in t], [1])
        self.assertEqual(t[0]["terms"], ["2.1%"])


class OverviewPagingTest(unittest.TestCase):
    def test_pages_cover_every_candidate(self):
        o = Orchestra.__new__(Orchestra)
        o.cands = {f"PMC{i}": {"source": "prefetch", "flags": [], "sentences": ["x"]} for i in range(97)}
        o.verdicts = {"PMC3": {"cites": "current"}}
        o.rounds = o.hops = 0
        seen = []
        first = o.overview()
        for p in range(1, first["n_pages"] + 1):
            seen += [r["work_id"] for r in o.overview(page=p)["candidates"]]
        self.assertEqual(len(seen), 97)
        self.assertEqual(first["n_pages"], -(-97 // config.OVERVIEW_PAGE_SIZE))
        self.assertEqual(o.overview(only_unjudged=True)["n_unjudged"], 96)
        self.assertNotIn("PMC3", [r["work_id"] for p in range(1, 4) for r in o.overview(page=p, only_unjudged=True)["candidates"]])


class _FakeAccess:
    """citations.access interface without Europe PMC: three candidates, every quote 'found'."""

    def __init__(self, ids=("PMC1", "PMC2", "PMC3"), match=None):
        self.calls, self.ids, self.match = [], list(ids), match or set(ids)

    def list_citers(self, terms_, since=None, exclude=()):
        self.calls.append("list")
        hits = {w: {"work_id": w, "date": f"2020-{i:04d}", "cites_target_version": ["published"]} for i, w in enumerate(self.ids)}
        return hits, {"per_version": {"published": {"hit_count": len(hits), "fetched": len(hits)}}, "search_truncated": False,
                      "no_open_full_text": 0, "citing_works_with_value": len(hits)}

    def screen(self, hits, terms_, source="prefetch"):
        self.calls.append(("screen", len(hits)))
        found = {w: {"work_id": w, "source": source, "flags": [], "sentences": [f"{w} uses 5.8 days"]} for w in hits if w in self.match}
        return found, {"screened": len(hits), "no_full_text": 0, "no_sentence": len(hits) - len(found)}

    def evidence(self, work_id, terms_, sentence=""):
        return {"reference_matched": True, "sentences_citing_target": [sentence], "sentences_with_old_value": [sentence],
                "worker_sentence_verbatim": True}

    def quotes_found(self, items):
        return [True for _ in items]

    def worker_tools(self):
        import types as pytypes
        return pytypes.SimpleNamespace(schemas=[], call=lambda name, args: "{}")


class _OrchBackend:
    """Scripted pro backend for the orchestra: orchestrator dispatches PMC1+PMC2 then finishes; workers say superseded;
    the program dispatches the leftover PMC3 to a worker, which says current; verifier confirms."""
    script = [[{"name": "overview", "args": {}}], [{"name": "dispatch", "args": {"groups": [["PMC1"], ["PMC2"]]}}],
              [{"name": "finish", "args": {"summary": "done"}}]]

    def __init__(self, state):
        self.state, self.model = state, "fake"
        self.calls = self.prompt_tokens = self.output_tokens = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        sys = messages[0]["content"]
        if sys.startswith("You are the lead citation analyst"):
            calls = self.script[self.state["orch"]]
            self.state["orch"] += 1
            return {"content": "", "tool_calls": calls, "assistant_message": {"role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": c["name"], "arguments": c["args"]}} for c in calls]}}
        if sys.startswith("You judge how specific"):
            w = next(x for x in ("PMC1", "PMC2", "PMC3") if x in messages[1]["content"])
            v = ({"work_id": w, "cites": "current", "role": "background", "sentence": "x"} if w == "PMC3" else
                 {"work_id": w, "cites": "superseded", "role": "model_input", "sentence": f"{w} uses 5.8 days"})
            text = json.dumps({"verdicts": [v]})
        else:  # verifier
            text = json.dumps({"checks": [{"work_id": w, "verdict": "confirm"} for w in ("PMC1", "PMC2")]})
        return {"content": text, "tool_calls": [], "assistant_message": {"role": "assistant", "content": text}}

    def tool_result_message(self, name, content):
        return {"role": "tool", "content": content, "tool_name": name}


class StepwiseOrchestraTest(unittest.TestCase):
    """部署方案 A8: a run split into steps, with the state round-tripped through JSON between steps (as citation_runs
    stores it), gives the same result as one run()."""

    def _target(self):
        return {"target_id": "t", "drift_event_id": "e", "first_author": "Backer", "terms": ["5.8 days"],
                "quantity": {"property_terms": ["incubation"], "unit": "days"},
                "drift": {"preprint_v1_claim": "5.8 days", "current_claim": "6.4 days"}}

    def _strip(self, res):
        return {k: v for k, v in res.items() if k != "orchestrate_s"}

    def test_steps_equal_run(self):
        st = {"orch": 0}
        whole = Orchestra(self._target(), access=_FakeAccess(), backend_factory=lambda: _OrchBackend(st)).run()
        st2 = {"orch": 0}
        o = Orchestra(self._target(), access=_FakeAccess(), backend_factory=lambda: _OrchBackend(st2))
        state, phases = json.loads(json.dumps(o.to_state())), []
        while state["phase"] != "done":
            o = Orchestra(state["target"], access=_FakeAccess(), backend_factory=lambda: _OrchBackend(st2), state=state)
            phases.append(o.step())
            state = json.loads(json.dumps(o.to_state()))
        self.assertEqual(phases, ["orchestrate", "orchestrate", "orchestrate", "overflow", "verify", "done"])  # 1st: prescreen batch
        self.assertEqual(self._strip(state["result"]), self._strip(whole))
        self.assertEqual(sorted((w["work_id"], w["cites"]) for w in whole["citing_works"]),
                         [("PMC1", "superseded"), ("PMC2", "superseded"), ("PMC3", "current")])
        self.assertEqual(whole["judged_by"], {"worker": 2, "auto_dispatch": 1})
        self.assertTrue(whole["coverage_complete"])
        self.assertEqual(whole["coverage_notes"], [])

    def test_leftovers_cut_at_worker_capacity(self):
        """Beyond WORKER_CAPACITY nothing is batch-judged: the leftover stays unjudged and the run says so."""
        from unittest import mock
        st = {"orch": 0}
        with mock.patch.object(config, "WORKER_CAPACITY", 2):
            res = Orchestra(self._target(), access=_FakeAccess(), backend_factory=lambda: _OrchBackend(st)).run()
        self.assertEqual(res["judged_by"], {"worker": 2, "auto_dispatch": 0})
        self.assertEqual([(u["work_id"], u["reason"]) for u in res["unjudged"]], [("PMC3", "worker capacity used up")])
        self.assertFalse(res["coverage_complete"])
        self.assertTrue(any("worker capacity (2 papers)" in n for n in res["coverage_notes"]))

    def test_large_target_screened_in_batches_and_cap_reported(self):
        """Guan-sized targets: screening runs in PRESCREEN_BATCH steps; beyond PRESCREEN_MAX_WORKS nothing is dropped
        silently -- the run is marked incomplete and says how many papers were not screened."""
        from unittest import mock
        ids = [f"PMC{i}" for i in range(25)]
        acc = _FakeAccess(ids=ids, match={"PMC1", "PMC2", "PMC3"})
        with mock.patch.object(config, "PRESCREEN_BATCH", 10), mock.patch.object(config, "PRESCREEN_MAX_WORKS", 20):
            res = Orchestra(self._target(), access=acc, backend_factory=lambda: _OrchBackend({"orch": 0})).run()
        self.assertEqual([c for c in acc.calls if c != "list"], [("screen", 10), ("screen", 10)])
        self.assertEqual(res["coverage"]["screened"], 20)
        self.assertEqual(res["coverage"]["screen_capped"], 5)
        self.assertFalse(res["coverage_complete"])
        self.assertTrue(any("5 of 25 matching papers were not screened" in n for n in res["coverage_notes"]))

    def test_search_more_searches_proposed_spellings(self):
        """Proposed spellings are searched as they are (no separate rule); already-searched ones are skipped."""
        acc = _FakeAccess()
        acc.search_more = lambda terms_, since=None, known=(): (acc.calls.append(("more", terms_)), ({}, False))[1]
        o = Orchestra({**self._target(), "terms": ["0 to 24"]}, access=acc, backend_factory=lambda: _OrchBackend({"orch": 0}))
        r = o.search_more("24 days, 0-24")
        self.assertEqual(r["already_searched"], ["0-24"])
        self.assertIn(("more", ["24 days"]), acc.calls)
        self.assertEqual(o.t["quantity"]["proposed"], ["24 days"])

    def test_failed_worker_does_not_end_the_run(self):
        """A worker whose model call fails (e.g. three timeouts) loses only its own papers: they stay unjudged with
        reason "no verdict returned" and the run finishes."""
        class Failing(_OrchBackend):
            def chat(self, messages, tools=None):
                if messages[0]["content"].startswith("You judge how specific") and "PMC2" in messages[1]["content"]:
                    raise TimeoutError("stalled")
                return super().chat(messages, tools)
        st = {"orch": 0}
        res = Orchestra(self._target(), access=_FakeAccess(), backend_factory=lambda: Failing(st)).run()
        self.assertEqual([(u["work_id"], u["reason"]) for u in res["unjudged"]], [("PMC2", "no verdict returned")])
        self.assertTrue(any("worker_failed" in e for e in res["events"]))
        self.assertFalse(res["coverage_complete"])

    def test_messages_round_trip_keeps_thought_signature(self):
        from google.genai import types
        from claimdrift import llm
        c = types.Content(role="model", parts=[types.Part(text="x", thought_signature=b"\x00\xffsig")])
        msgs = llm.load_messages(json.loads(json.dumps(llm.dump_messages([{"role": "assistant", "content": "x", "_gemini_content": c}]))))
        self.assertEqual(msgs[0]["_gemini_content"].parts[0].thought_signature, b"\x00\xffsig")


class SerialTest(unittest.TestCase):
    def test_pair_round_trip(self):
        from claimdrift import serial
        pair = load_pair(CASE)
        back = serial.pair_from_dict(json.loads(json.dumps(serial.pair_to_dict(pair))))
        self.assertEqual(back.preprint.full_text(), pair.preprint.full_text())
        self.assertEqual(back.published.outline(), pair.published.outline())
        self.assertEqual((back.paper_id, back.preprint_doi, back.pre_id), (pair.paper_id, pair.preprint_doi, pair.pre_id))


class SelfcheckValueTest(unittest.TestCase):
    def test_old_or_new(self):
        pre, pub = "mean incubation period 5.8 days (4.6-7.9)", "mean incubation period 6.4 days (5.6-7.7)"
        self.assertEqual(old_or_new("we assumed 5.8 days [12]", pre, pub)["verdict"], "uses_old_value")
        self.assertEqual(old_or_new("estimated at 6.4 days [12]", pre, pub)["verdict"], "uses_current_value")
        self.assertEqual(old_or_new("5.8 days, later revised to 6.4 days", pre, pub)["verdict"], "mentions_both")
        self.assertEqual(old_or_new("around a week", pre, pub)["verdict"], "cannot_tell")
        self.assertEqual(old_or_new("5.80 days (Backer et al.)", pre, pub)["verdict"], "uses_old_value")
        self.assertEqual(old_or_new("5·8 days [3]", pre, pub)["verdict"], "uses_old_value")  # middle dot, citation marker
        self.assertEqual(old_or_new("5.8 (95% CI 4.6-7.9)", pre, pub)["verdict"], "uses_old_value")  # number without its unit
        self.assertEqual(old_or_new("5.8% of patients", pre, pub)["verdict"], "cannot_tell")  # wrong unit
        # digits that are not quantities: disease names, years, confidence levels, figure/citation numbers
        pre2, pub2 = "incubation period of 19 days in 2 of 7 patients", "incubation period of 21 days in 2 of 7 patients"
        self.assertEqual(old_or_new("COVID-19 patients (95% CI) [19] in 2020, see Figure 7", pre2, pub2)["verdict"], "cannot_tell")
        self.assertEqual(old_or_new("a 19-day incubation period", pre2, pub2)["verdict"], "uses_old_value")
        # a range shares its unit; thousands separators
        self.assertEqual(old_or_new("quarantine of 9 to 14 days", "quarantine for 9 days", "quarantine for 14 days")["verdict"], "mentions_both")
        self.assertEqual(old_or_new("29,500 infections", "29,500 cases by Jan 29", "21,000 cases by Jan 29")["verdict"], "uses_old_value")

    def test_hints(self):
        from claimdrift.selfcheck import hints
        h = {"first_author": "backer", "preprint_doi": "10.1101/2020.01.27.20018986",
             "preprint_text": "mean 5.8 days; n = 88", "published_text": "mean 6.4 days; n = 88"}
        self.assertEqual(hints("we used 5.8 days", h, {"5.8 days"})[0], ["value"])
        self.assertEqual(hints("88 travellers", h, {"88"})[0], ["shared_value"])
        self.assertEqual(hints("Backer et al. (doi:10.1101/2020.01.27.20018986)", h, set())[0], ["author", "doi"])
        self.assertEqual(hints("Backerman et al.", h, set())[0], [])

    def test_judge_parsing(self):
        from claimdrift.selfcheck import judge_candidates

        class Fake:
            def __init__(self, text):
                self.text = text

            def chat(self, messages, tools=None):
                return {"content": self.text}
        cands = [{"hints": []}, {"hints": ["value"]}]
        out = judge_candidates("s", cands, Fake('{"judgements": [{"id": "c2", "refers": "yes", "value_use": "uses_old_value"},'
                                                 ' {"id": "c9", "refers": "yes"}, {"id": "c1", "refers": "maybe", "value_use": "?"}]}'))
        self.assertEqual(out[1]["refers"], "yes")
        self.assertEqual(out[1]["value_use"], "uses_old_value")
        self.assertEqual((out[0]["refers"], out[0]["value_use"]), ("no", "cannot_tell"))  # invalid labels -> safe defaults
        self.assertNotIn(8, out)  # ids outside the candidate list are ignored
        self.assertIsNone(judge_candidates("s", cands, Fake("no json here")))
        self.assertEqual(judge_candidates("s", [], Fake("")), {})

    def test_claim_values(self):
        from claimdrift.selfcheck import claim_values
        self.assertEqual(claim_values("50.7% of 148 patients; R0 of 5.47 (SARS-CoV-2, 2020)"), {"50.7%", "148 patients", "5.47"})
        self.assertEqual(claim_values("2 to 14 days, p<0.001, Table 2, IL-6 and CD4 counts"), {"2 days", "14 days"})
        self.assertEqual(claim_values("Figure 3 [7] in 2021"), set())

    def test_preprints_not_in_library(self):
        from unittest import mock
        from claimdrift import selfcheck
        refs = ("1. backer ja. medrxiv 2020. doi: 10.1101/2020.01.27.20018986. 2. li q. n engl j med. 10.1056/nejmoa2001316 "
                "3. wu jt. biorxiv 10.1101/2020.02.10.942011v2). 4. again 10.1101/2020.02.10.942011.")
        known = {"10.1101/2020.01.27.20018986": {"event_id": "E"}}
        with mock.patch.object(selfcheck, "_events_by_doi", lambda dois: {d: known[d] for d in dois if d in known}):
            # journal DOIs ignored, library preprints excluded, version suffix and trailing punctuation dropped, deduplicated
            self.assertEqual(selfcheck.preprints_not_in_library(refs), ["10.1101/2020.02.10.942011"])
            self.assertEqual(selfcheck.preprints_not_in_library(""), [])


if __name__ == "__main__":
    unittest.main()
