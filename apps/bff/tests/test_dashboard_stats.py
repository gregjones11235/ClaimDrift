"""Tests for the BFF dashboard_stats() rollups (server.py).

Two data sources are covered:

  - SeedDataSource: runs against the checked-in demo_seed JSON. Needs no ES,
    so it runs in CI / locally without credentials. Asserts the shape and a
    few invariants (sent <= total, counts are non-negative ints).

  - ElasticDataSource: constructed with a fake in-memory client that records
    the requests it receives and returns canned ES aggregation responses. This
    locks the query SHAPE (size:0 + track_total_hits + the specific aggs) and
    proves the per-rollup try/except degrades to 0/None instead of raising —
    the property that keeps a bad field mapping from 500-ing the dashboard.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT))

from apps.bff.server import ElasticDataSource, SeedDataSource

STATS_KEYS = {
    "drift_events_total",
    "high_severity_count",
    "avg_materiality_score",
    "affected_citations_total",
    "notifications_total",
    "notifications_sent",
    "patterns_total",
    "top_pattern_type",
}


class TestSeedDashboardStats(unittest.TestCase):
    def setUp(self) -> None:
        self.stats = SeedDataSource().dashboard_stats()

    def test_has_all_keys(self) -> None:
        self.assertEqual(set(self.stats.keys()), STATS_KEYS)

    def test_counts_are_non_negative_ints(self) -> None:
        for key in (
            "drift_events_total",
            "high_severity_count",
            "affected_citations_total",
            "notifications_total",
            "notifications_sent",
            "patterns_total",
        ):
            self.assertIsInstance(self.stats[key], int, key)
            self.assertGreaterEqual(self.stats[key], 0, key)

    def test_sent_not_more_than_total(self) -> None:
        self.assertLessEqual(self.stats["notifications_sent"], self.stats["notifications_total"])

    def test_high_severity_not_more_than_total(self) -> None:
        self.assertLessEqual(self.stats["high_severity_count"], self.stats["drift_events_total"])

    def test_avg_score_is_float_in_range(self) -> None:
        self.assertIsInstance(self.stats["avg_materiality_score"], float)
        self.assertGreaterEqual(self.stats["avg_materiality_score"], 0.0)
        self.assertLessEqual(self.stats["avg_materiality_score"], 1.0)


class _FakeClient:
    """Records (method, path, body) and returns a canned response per index."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, path: str, body: object = None) -> object:
        self.calls.append((method, path, body))
        for key, resp in self.responses.items():
            if key in path:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return {}


def _make_es(responses: dict[str, object]) -> ElasticDataSource:
    # Bypass __init__ (which builds a real ElasticsearchHttpClient needing env).
    ds = ElasticDataSource.__new__(ElasticDataSource)
    ds.client = _FakeClient(responses)
    return ds


class TestElasticDashboardStatsHappyPath(unittest.TestCase):
    def setUp(self) -> None:
        self.responses = {
            "/drift_events/_search": {
                "hits": {"total": {"value": 1280}},
                "aggregations": {
                    "avg_materiality": {"value": 0.47},
                    "high_severity": {"doc_count": 35},
                },
            },
            "/affected_citations/_count": {"count": 248},
            "/notification_log/_search": {
                "hits": {"total": {"value": 220}},
                "aggregations": {
                    "by_status": {
                        "buckets": [
                            {"key": "sent", "doc_count": 204},
                            {"key": "skipped", "doc_count": 16},
                        ]
                    }
                },
            },
            "/drift_patterns/_search": {
                "hits": {"total": {"value": 63}},
                "aggregations": {
                    "by_type": {"buckets": [{"key": "claim_disappearance", "doc_count": 40}]}
                },
            },
        }
        self.ds = _make_es(self.responses)
        self.stats = self.ds.dashboard_stats()

    def test_values_mapped_from_es(self) -> None:
        self.assertEqual(self.stats["drift_events_total"], 1280)
        self.assertEqual(self.stats["high_severity_count"], 35)
        self.assertAlmostEqual(self.stats["avg_materiality_score"], 0.47)
        self.assertEqual(self.stats["affected_citations_total"], 248)
        self.assertEqual(self.stats["notifications_total"], 220)
        self.assertEqual(self.stats["notifications_sent"], 204)
        self.assertEqual(self.stats["patterns_total"], 63)
        self.assertEqual(self.stats["top_pattern_type"], "claim_disappearance")

    def test_query_shape_is_aggregation_not_full_scan(self) -> None:
        # The whole point of the fix: rollups are size:0 aggregations, not a
        # fetch of every document. Assert size:0 + track_total_hits on the
        # _search calls.
        search_bodies = [
            body for (_m, path, body) in self.ds.client.calls if path.endswith("/_search")
        ]
        self.assertTrue(search_bodies)
        for body in search_bodies:
            self.assertEqual(body.get("size"), 0, body)
            self.assertTrue(body.get("track_total_hits"), body)
            self.assertIn("aggs", body)

    def test_sent_bucket_absent_yields_zero(self) -> None:
        responses = dict(self.responses)
        responses["/notification_log/_search"] = {
            "hits": {"total": {"value": 10}},
            "aggregations": {"by_status": {"buckets": [{"key": "drafted", "doc_count": 10}]}},
        }
        stats = _make_es(responses).dashboard_stats()
        self.assertEqual(stats["notifications_total"], 10)
        self.assertEqual(stats["notifications_sent"], 0)


class TestElasticDashboardStatsDegradesGracefully(unittest.TestCase):
    def test_all_aggs_failing_returns_zeros_not_exception(self) -> None:
        boom = {
            "/drift_events/_search": RuntimeError("unmapped field"),
            "/affected_citations/_count": RuntimeError("index missing"),
            "/notification_log/_search": RuntimeError("no fielddata"),
            "/drift_patterns/_search": RuntimeError("boom"),
        }
        ds = _make_es(boom)
        stats = ds.dashboard_stats()  # must NOT raise
        self.assertEqual(set(stats.keys()), STATS_KEYS)
        self.assertEqual(stats["drift_events_total"], 0)
        self.assertEqual(stats["affected_citations_total"], 0)
        self.assertEqual(stats["notifications_total"], 0)
        self.assertEqual(stats["notifications_sent"], 0)
        self.assertEqual(stats["patterns_total"], 0)
        self.assertIsNone(stats["top_pattern_type"])
        self.assertEqual(stats["avg_materiality_score"], 0.0)

    def test_partial_failure_keeps_other_rollups(self) -> None:
        # drift_events agg fails, but citations count still succeeds.
        responses = {
            "/drift_events/_search": RuntimeError("unmapped field"),
            "/affected_citations/_count": {"count": 99},
            "/notification_log/_search": {"hits": {"total": {"value": 0}}, "aggregations": {"by_status": {"buckets": []}}},
            "/drift_patterns/_search": {"hits": {"total": {"value": 0}}, "aggregations": {"by_type": {"buckets": []}}},
        }
        stats = _make_es(responses).dashboard_stats()
        self.assertEqual(stats["drift_events_total"], 0)  # degraded
        self.assertEqual(stats["affected_citations_total"], 99)  # survived


if __name__ == "__main__":
    unittest.main()
