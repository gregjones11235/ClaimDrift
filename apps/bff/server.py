import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol, Optional
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from ingestion.common.elastic import ElasticsearchHttpClient
from apps.bff.sse_adapter import TranslatorState, heartbeat, translate_adk_event


SEED_DIR = ROOT / "elastic" / "demo_seed"
GOLDEN_STREAM = ROOT / "apps" / "dispatcher" / "tests" / "golden" / "stream_amblyopia_v2.jsonl"
# Cloud Run injects $PORT (default 8080); local dev uses $BFF_PORT (default 8787).
# Honor $PORT first so the same image runs unchanged on Cloud Run.
PORT = int(os.getenv("PORT") or os.getenv("BFF_PORT", "8787"))
# Bind 0.0.0.0 in a container (Cloud Run requires it); loopback only for local dev.
HOST = os.getenv("BFF_HOST", "0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
INCLUDE_DEMO_RECORDS = os.getenv("BFF_INCLUDE_DEMO", "").lower() in {"1", "true", "yes"}
REPLAY_GOLDEN = os.getenv("SSE_REPLAY_GOLDEN", "").lower() in {"1", "true", "yes"}
SSE_TAIL_POLL_INTERVAL_S = float(os.getenv("SSE_TAIL_POLL_S", "1.0"))
SSE_TAIL_TIMEOUT_S = float(os.getenv("SSE_TAIL_TIMEOUT_S", "300"))
SSE_HEARTBEAT_INTERVAL_S = float(os.getenv("SSE_HEARTBEAT_S", "15"))
# New system: every read goes through claimdrift.es, which targets Elasticsearch Serverless by default and the local
# docker node with CLAIMDRIFT_LOCAL=1 (claimdrift/config.py). BFF_SEED_DATA=1 serves the bundled demo seed instead.
SEED_ONLY = os.getenv("BFF_SEED_DATA", "").lower() in {"1", "true", "yes"}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_rows(name: str) -> list[dict]:
    path = SEED_DIR / f"{name}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def es_hits(response: dict) -> list[dict]:
    return [hit.get("_source", {}) for hit in response.get("hits", {}).get("hits", [])]


class DataSource(Protocol):
    mode: str

    def drift_events(self) -> list[dict]:
        ...

    def drift_event(self, event_id: str) -> Optional[dict]:
        ...

    def claims_for_event(self, event: dict) -> list[dict]:
        ...

    def affected_citations(self, event_id: str) -> list[dict]:
        ...

    def notifications(self, event_id: str) -> list[dict]:
        ...

    def dashboard_stats(self) -> dict:
        ...


class SeedDataSource:
    mode = "seed"

    def drift_events(self) -> list[dict]:
        return load_rows("drift_events")

    def drift_event(self, event_id: str) -> Optional[dict]:
        return next((row for row in self.drift_events() if row["event_id"] == event_id), None)

    def claims_for_event(self, event: dict) -> list[dict]:
        claim_ids = claim_ids_for_event(event)
        return [row for row in load_rows("claims") if row["claim_id"] in claim_ids]

    def affected_citations(self, event_id: str) -> list[dict]:
        return [row for row in load_rows("affected_citations") if row["drift_event_id"] == event_id]

    def notifications(self, event_id: str) -> list[dict]:
        return [row for row in load_rows("notification_log") if row["drift_event_id"] == event_id]

    def dashboard_stats(self) -> dict:
        # Seed mode has small local JSON; computing in Python is fine and keeps
        # the BFF runnable without ES credentials (local dev / CI).
        events = load_rows("drift_events")
        scores = [e["materiality_score"] for e in events if "materiality_score" in e]
        notifications = load_rows("notification_log")
        citations = load_rows("affected_citations")
        return {
            "drift_events_total": len(events),
            "high_severity_count": sum(1 for s in scores if s >= 0.7),
            "avg_materiality_score": (sum(scores) / len(scores)) if scores else 0.0,
            "affected_citations_total": len(citations),
            "notifications_total": len(notifications),
            "notifications_sent": sum(1 for n in notifications if n.get("status") == "sent"),
            "review_pending_total": sum(1 for r in events + citations if r.get("review_status") == "pending"),
            "superseded_citations_total": sum(1 for c in citations if c.get("cites") == "superseded"),
        }


class ElasticDataSource:
    mode = "elastic"

    def __init__(self) -> None:
        self.client = ElasticsearchHttpClient()

    def visible_query(self, query: dict) -> dict:
        # Two visibility filters are layered here:
        #   1. demo_seed records — hidden unless BFF_INCLUDE_DEMO is set.
        #   2. suspected false positives — ALWAYS hidden (independent of the
        #      demo toggle). These are drift_events whose published side had a
        #      title-placeholder abstract (Crossref returned no abstract, so
        #      ingestion fell back to the title), making drift_analyzer flag
        #      every claim as "disappeared" and inflate materiality. 105 such
        #      events were tagged 2026-06-09; see records.py and the
        #      published-abstract-missing bug. `suspected_false_positive` is a
        #      boolean field only on drift_events; a must_not term on indices
        #      that lack it (affected_citations/notifications/patterns/claims)
        #      simply matches nothing, so this filter is a no-op there.
        must_not: list[dict] = [{"term": {"suspected_false_positive": True}}]
        if not INCLUDE_DEMO_RECORDS:
            must_not.append({"term": {"record_source": "demo_seed"}})
        return {"bool": {"must": [query], "must_not": must_not}}

    def search(self, index_name: str, body: dict) -> list[dict]:
        return es_hits(self.client.request("POST", f"/{index_name}/_search", body))

    def drift_events(self) -> list[dict]:
        return self.search(
            "drift_events",
            {
                "query": self.visible_query({"match_all": {}}),
                "size": 100,
                "sort": [{"detected_at": {"order": "desc", "unmapped_type": "date"}}],
            },
        )

    def drift_event(self, event_id: str) -> Optional[dict]:
        rows = self.search(
            "drift_events",
            {"query": self.visible_query({"term": {"event_id": event_id}}), "size": 1},
        )
        return rows[0] if rows else None

    def claims_for_event(self, event: dict) -> list[dict]:
        claim_ids = sorted(claim_ids_for_event(event))
        if not claim_ids:
            return []
        return self.search(
            "claims",
            {"query": self.visible_query({"terms": {"claim_id": claim_ids}}), "size": len(claim_ids)},
        )

    def affected_citations(self, event_id: str) -> list[dict]:
        return self.search(
            "affected_citations",
            {"query": self.visible_query({"term": {"drift_event_id": event_id}}), "size": 100},
        )

    def notifications(self, event_id: str) -> list[dict]:
        return self.search(
            "notification_log",
            {"query": self.visible_query({"term": {"drift_event_id": event_id}}), "size": 100},
        )

    def _agg_search(self, index_name: str, body: dict) -> dict:
        """Run a size:0 aggregation/count search and return the raw ES response.

        Unlike `search()` (which unwraps to `hits.hits._source`), dashboard
        stats need `hits.total` and the `aggregations` block, so we return the
        full response here.
        """
        return self.client.request("POST", f"/{index_name}/_search", body)

    def _total_hits(self, response: dict) -> int:
        # track_total_hits:true makes hits.total.value the exact count.
        return int(((response.get("hits") or {}).get("total") or {}).get("value") or 0)

    def dashboard_stats(self) -> dict:
        """Whole-index rollups via ES aggregations — 3 queries, not N+1.

        Each sub-rollup is independently guarded: if a field is unmapped or a
        terms agg has no fielddata, that metric degrades to 0/None rather than
        500-ing the whole dashboard. `materiality_score` (numeric) and the
        keyword fields aggregate cleanly on our mappings (contracts §2.2.x);
        the .keyword fallback + try/except is defense-in-depth.
        """
        match = self.visible_query({"match_all": {}})

        drift_events_total = 0
        high_severity_count = 0
        avg_materiality_score = 0.0
        review_pending_total = 0
        try:
            resp = self._agg_search(
                "drift_events",
                {
                    "size": 0,
                    "track_total_hits": True,
                    "query": match,
                    "aggs": {
                        "avg_materiality": {"avg": {"field": "materiality_score"}},
                        "high_severity": {
                            "filter": {"range": {"materiality_score": {"gte": 0.7}}}
                        },
                        "review_pending": {"filter": {"term": {"review_status": "pending"}}},
                    },
                },
            )
            drift_events_total = self._total_hits(resp)
            aggs = resp.get("aggregations") or {}
            avg_materiality_score = float((aggs.get("avg_materiality") or {}).get("value") or 0.0)
            high_severity_count = int((aggs.get("high_severity") or {}).get("doc_count") or 0)
            review_pending_total += int((aggs.get("review_pending") or {}).get("doc_count") or 0)
        except Exception as exc:  # noqa: BLE001 - never let stats 500 the dashboard
            print(f"dashboard_stats: drift_events agg failed: {exc}")

        affected_citations_total = 0
        try:
            resp = self.client.request(
                "POST", "/affected_citations/_count", {"query": match}
            )
            affected_citations_total = int(resp.get("count") or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"dashboard_stats: affected_citations count failed: {exc}")

        notifications_total = 0
        notifications_sent = 0
        try:
            resp = self._agg_search(
                "notification_log",
                {
                    "size": 0,
                    "track_total_hits": True,
                    "query": match,
                    "aggs": {"by_status": {"terms": {"field": "status", "size": 20}}},
                },
            )
            notifications_total = self._total_hits(resp)
            buckets = (((resp.get("aggregations") or {}).get("by_status") or {}).get("buckets")) or []
            notifications_sent = next(
                (int(b.get("doc_count") or 0) for b in buckets if b.get("key") == "sent"), 0
            )
        except Exception as exc:  # noqa: BLE001
            print(f"dashboard_stats: notification_log agg failed: {exc}")

        superseded_citations_total = 0
        try:
            resp = self._agg_search(
                "affected_citations",
                {
                    "size": 0,
                    "track_total_hits": True,
                    "query": match,
                    "aggs": {
                        "review_pending": {"filter": {"term": {"review_status": "pending"}}},
                        "superseded": {"filter": {"term": {"cites": "superseded"}}},
                    },
                },
            )
            aggs = resp.get("aggregations") or {}
            review_pending_total += int((aggs.get("review_pending") or {}).get("doc_count") or 0)
            superseded_citations_total = int((aggs.get("superseded") or {}).get("doc_count") or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"dashboard_stats: affected_citations agg failed: {exc}")

        return {
            "drift_events_total": drift_events_total,
            "high_severity_count": high_severity_count,
            "avg_materiality_score": avg_materiality_score,
            "affected_citations_total": affected_citations_total,
            "notifications_total": notifications_total,
            "notifications_sent": notifications_sent,
            "review_pending_total": review_pending_total,
            "superseded_citations_total": superseded_citations_total,
        }


def claim_ids_for_event(event: dict) -> set[str]:
    claim_ids = set()
    for diff in event.get("claim_diffs", []):
        if diff.get("preprint_claim_id"):
            claim_ids.add(diff["preprint_claim_id"])
        if diff.get("published_claim_id"):
            claim_ids.add(diff["published_claim_id"])
    return claim_ids


class ClaimdriftESClient:
    """claimdrift.es (cloud by default, local with CLAIMDRIFT_LOCAL=1); same request() contract as ElasticsearchHttpClient."""

    def request(self, method: str, path: str, body: Optional[Any] = None) -> Any:
        from claimdrift import es as cd_es
        try:
            return cd_es.request(method, path, body)
        except cd_es.ESError as exc:
            raise RuntimeError(str(exc)) from exc


def build_data_source() -> DataSource:
    if SEED_ONLY:
        return SeedDataSource()
    from claimdrift import config as cd_config
    ds = ElasticDataSource.__new__(ElasticDataSource)
    ds.client = ClaimdriftESClient()
    ds.mode = "elastic-local" if cd_config.LOCAL else "elastic-cloud"
    return ds


DATA_SOURCE = build_data_source()


def send_json(handler: BaseHTTPRequestHandler, status: int, body: object) -> None:
    data = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    def handle(self) -> None:
        try:
            super().handle()
        except ConnectionResetError:
            return

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Last-Event-ID")
        self.end_headers()

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path == "/api/health":
                send_json(
                    self,
                    200,
                    {
                        "status": "ok",
                        "data_source": DATA_SOURCE.mode,
                        "include_demo_records": INCLUDE_DEMO_RECORDS,
                    },
                )
                return

            if path == "/api/events/stream":
                self.stream_events(parse_qs(parsed.query))
                return

            if path == "/api/stats":
                send_json(self, 200, DATA_SOURCE.dashboard_stats())
                return

            if path == "/api/drift-events":
                # `items` is intentionally capped (most-recent 100) for the
                # table; `count` reports the true index total so callers reading
                # the count don't see the 100-cap as the population size.
                events = DATA_SOURCE.drift_events()
                try:
                    total = DATA_SOURCE.dashboard_stats()["drift_events_total"]
                except Exception:  # noqa: BLE001 - fall back to page length
                    total = len(events)
                send_json(self, 200, {"items": events, "count": total})
                return

            if path.startswith("/api/drift-events/"):
                parts = path.split("/")
                event_id = parts[3]
                suffix = parts[4] if len(parts) > 4 else None
                self.handle_drift_event(event_id, suffix)
                return

            if self.handle_new_system_get(path, parse_qs(parsed.query)):
                return

            send_json(self, 404, {"error": "not_found"})
        except RuntimeError as exc:
            send_json(self, 502, {"error": "elasticsearch_error", "message": str(exc)})

    # ---- new system: review queue (P1.8) + author self-check (P1.10); routes in review_api.py ----
    def _new_system_api(self):
        if SEED_ONLY:
            send_json(self, 503, {"error": "elasticsearch_required",
                                  "message": "review and self-check need Elasticsearch (unset BFF_SEED_DATA)"})
            return None
        from apps.bff import review_api
        return review_api

    def _api_call(self, fn, *args) -> None:
        from apps.bff.review_api import ApiError
        try:
            send_json(self, 200, fn(*args))
        except ApiError as exc:
            send_json(self, exc.status, {"error": exc.error, "message": exc.message})

    def handle_new_system_get(self, path: str, query: dict[str, list[str]]) -> bool:
        parts = [unquote(p) for p in path.split("/")]
        if path == "/api/review-queue":
            api = self._new_system_api()
            if api:
                self._api_call(api.review_queue, query.get("kind", ["all"])[0], query.get("status", ["pending"])[0])
            return True
        if len(parts) == 5 and parts[2] == "review" and parts[3] in ("events", "citations"):
            api = self._new_system_api()
            if api:
                fn = api.review_event_detail if parts[3] == "events" else api.review_citation_detail
                self._api_call(fn, parts[4])
            return True
        if len(parts) == 5 and parts[2] == "drift-events" and parts[4] == "citation-runs":
            api = self._new_system_api()
            if api:
                self._api_call(api.citation_runs, parts[3])
            return True
        if path == "/api/selfcheck/analyze":
            api = self._new_system_api()
            if api:
                self._api_call(api.selfcheck_analyze_status, query.get("doi", [""])[0])
            return True
        return False

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path.rstrip("/")
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                send_json(self, 413, {"error": "payload_too_large"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except json.JSONDecodeError:
                send_json(self, 400, {"error": "invalid_json"})
                return
            if not isinstance(body, dict):
                send_json(self, 400, {"error": "body_must_be_object"})
                return
            parts = [unquote(p) for p in path.split("/")]
            if not path.startswith(("/api/review/", "/api/selfcheck/")):
                send_json(self, 404, {"error": "not_found"})
                return
            api = self._new_system_api()
            if api is None:
                return
            if len(parts) == 5 and parts[2] == "review" and parts[3] in ("events", "citations"):
                self._api_call(api.decide, parts[3], parts[4], body)
            elif path == "/api/selfcheck/published":
                self._api_call(api.selfcheck_published, body)
            elif path == "/api/selfcheck/sentences":
                self._api_call(api.selfcheck_sentences, body)
            elif path == "/api/selfcheck/references":
                self._api_call(api.selfcheck_references, body)
            elif path == "/api/selfcheck/analyze":
                self._api_call(api.selfcheck_analyze, body)
            else:
                send_json(self, 404, {"error": "not_found"})
        except RuntimeError as exc:
            send_json(self, 502, {"error": "elasticsearch_error", "message": str(exc)})

    def handle_drift_event(self, event_id: str, suffix: Optional[str]) -> None:
        event = DATA_SOURCE.drift_event(event_id)
        if event is None:
            send_json(self, 404, {"error": "drift_event_not_found"})
            return

        if suffix is None:
            send_json(self, 200, event)
            return

        if suffix == "claims":
            rows = DATA_SOURCE.claims_for_event(event)
            send_json(self, 200, {"items": rows, "count": len(rows)})
            return

        if suffix == "affected-citations":
            rows = DATA_SOURCE.affected_citations(event_id)
            send_json(self, 200, {"items": rows, "count": len(rows)})
            return

        if suffix == "notifications":
            rows = DATA_SOURCE.notifications(event_id)
            send_json(self, 200, {"items": rows, "count": len(rows)})
            return

        if suffix == "citation-runs":  # new system (review_api); this branch is reached before handle_new_system_get
            api = self._new_system_api()
            if api:
                self._api_call(api.citation_runs, event_id)
            return

        send_json(self, 404, {"error": "unknown_drift_event_view"})

    def stream_events(self, query: dict[str, list[str]]) -> None:
        """SSE channel for the agent activity timeline. Three modes:

          1. Live tail of `agent_events` (Elastic mode): dispatcher writes
             translated §6.1 envelopes as the supervisor stream progresses.
             We poll the index and flush new rows. Resumable via Last-Event-ID.
          2. Golden replay (SSE_REPLAY_GOLDEN=1): replay the T1 reference
             stream through the same translator. Demonstrates production
             event flow without GCP credentials — for evaluator reproduction.
          3. Static fallback (seed mode, no replay): hand-coded 9 events.
             Kept only for the case where neither ES nor golden file exists.
        """
        drift_event_id = query.get("drift_event_id", ["demo-drift-001"])[0]
        dispatch_id = query.get("dispatch_id", [""])[0]
        last_event_id = self.headers.get("Last-Event-ID") or query.get("last_event_id", ["0"])[0]
        try:
            resume_seq = int(last_event_id)
        except (TypeError, ValueError):
            resume_seq = 0

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            if REPLAY_GOLDEN and GOLDEN_STREAM.exists():
                self._stream_replay_golden(drift_event_id)
            elif isinstance(DATA_SOURCE, ElasticDataSource):
                self._stream_es_tail(DATA_SOURCE, drift_event_id, dispatch_id, resume_seq)
            else:
                self._stream_static_fallback(drift_event_id)
        except BrokenPipeError:
            return
        except Exception:
            # Surface adapter-side failures as §6.1 agent.failed before bailing,
            # so the frontend can show a real error state instead of an opaque
            # disconnect.
            import traceback
            err = {
                "event_type": "agent.failed",
                "agent_id": None,
                "drift_event_id": drift_event_id,
                "timestamp": now(),
                "payload": {"error_message": traceback.format_exc(limit=2)[:500], "retry_count": 0},
            }
            try:
                self._write_sse(0, err)
            except BrokenPipeError:
                pass

    # --- SSE writers --------------------------------------------------------

    def _write_sse(self, seq: int, envelope: dict[str, Any]) -> None:
        out = []
        if seq:
            out.append(f"id: {seq}\n")
        out.append(f"event: {envelope['event_type']}\n")
        out.append(f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n")
        self.wfile.write("".join(out).encode("utf-8"))
        self.wfile.flush()

    def _stream_static_fallback(self, drift_event_id: str) -> None:
        """Fallback (seed mode, no replay file). Hand-coded event sequence in the new system's order."""
        events = [
            ("agent.started", "drift_analyzer", {"input_summary": "Comparing preprint v1 with the published full text (one call)."}),
            ("agent.completed", "drift_analyzer", {"output_summary": "Numerical shift detected; evidence quotes verified.", "output_id": drift_event_id}),
            ("agent.started", "citation_finder", {"input_summary": "Screening Europe PMC citing papers for the superseded value."}),
            ("agent.completed", "citation_finder", {"output_summary": "1 citing paper relies on the old value (awaiting review).", "output_id": "affected_citations"}),
            ("agent.started", "notifier", {"input_summary": "Drafting notification after human approval."}),
            ("agent.completed", "notifier", {"output_summary": "1 notification drafted.", "output_id": "notification_log"}),
        ]
        for idx, (event_type, agent_id, payload) in enumerate(events, start=1):
            self._write_sse(idx, {
                "event_type": event_type,
                "agent_id": agent_id,
                "drift_event_id": drift_event_id,
                "timestamp": now(),
                "payload": payload,
            })
            time.sleep(0.8)
        self._write_sse(0, heartbeat(drift_event_id))

    def _stream_replay_golden(self, drift_event_id: str) -> None:
        """Replay the checked-in T1 stream through the production translator.

        Same code path the dispatcher uses, so what evaluators see in the
        frontend is bit-identical to what the dispatcher would write to ES
        on a real GCP run. Paces events 0.6s apart so the timeline animates.
        """
        state = TranslatorState()
        seq = 0
        with GOLDEN_STREAM.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    adk_event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                envelopes = translate_adk_event(adk_event, state, drift_event_id=drift_event_id)
                for env in envelopes:
                    seq += 1
                    self._write_sse(seq, env)
                    time.sleep(0.6)
        self._write_sse(0, heartbeat(drift_event_id))

    def _stream_es_tail(
        self,
        ds: "ElasticDataSource",
        drift_event_id: str,
        dispatch_id: str,
        resume_seq: int,
    ) -> None:
        """Tail `agent_events` for one drift_event_id (or dispatch_id) until
        the supervisor stream completes or the tail times out.

        Termination signals (any one ends the stream):
          - last envelope in DB has event_type == agent.completed AND
            agent_id == memory_synthesizer (§4.1 final phase), and no new
            events arrive for one poll interval after that.
          - the drift_events row for drift_event_id exists AND we haven't
            seen a new agent_events row for 3 consecutive polls.
          - SSE_TAIL_TIMEOUT_S elapsed.

        Heartbeats are interleaved every SSE_HEARTBEAT_INTERVAL_S to keep
        intermediaries (nginx, browsers) from closing idle connections.
        """
        query: dict[str, Any]
        if dispatch_id:
            query = {"term": {"dispatch_id": dispatch_id}}
        else:
            query = {"term": {"drift_event_id": drift_event_id}}

        seq = resume_seq
        last_event_ts = time.monotonic()
        last_heartbeat_ts = time.monotonic()
        deadline = time.monotonic() + SSE_TAIL_TIMEOUT_S
        seen_memory_completed = False
        idle_polls_after_completion = 0

        while time.monotonic() < deadline:
            try:
                rows = ds.search(
                    "agent_events",
                    {
                        "query": {
                            "bool": {
                                "must": [query, {"range": {"event_seq": {"gt": seq}}}],
                            }
                        },
                        "size": 200,
                        "sort": [{"event_seq": {"order": "asc"}}],
                    },
                )
            except RuntimeError:
                # Index may not exist yet on a fresh deploy; treat as empty.
                rows = []

            if rows:
                last_event_ts = time.monotonic()
                idle_polls_after_completion = 0
                for row in rows:
                    next_seq = row.get("event_seq", seq + 1)
                    envelope = {
                        "event_type": row.get("event_type"),
                        "agent_id": row.get("agent_id"),
                        "drift_event_id": row.get("drift_event_id"),
                        "timestamp": row.get("timestamp"),
                        "payload": row.get("payload") or {},
                    }
                    self._write_sse(next_seq, envelope)
                    seq = next_seq
                    if envelope["event_type"] == "agent.completed" and envelope["agent_id"] == "memory_synthesizer":
                        seen_memory_completed = True
            else:
                if seen_memory_completed:
                    idle_polls_after_completion += 1
                    if idle_polls_after_completion >= 2:
                        break

            if time.monotonic() - last_heartbeat_ts >= SSE_HEARTBEAT_INTERVAL_S:
                self._write_sse(0, heartbeat(drift_event_id))
                last_heartbeat_ts = time.monotonic()

            time.sleep(SSE_TAIL_POLL_INTERVAL_S)

        self._write_sse(0, heartbeat(drift_event_id))

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    if not (SEED_DIR / "drift_events.json").exists():
        print("Demo seed data is missing. Run: python3 elastic/scripts/seed_demo_cases.py")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"ClaimDrift BFF running at http://{HOST}:{PORT} ({DATA_SOURCE.mode} data source)")
    server.serve_forever()


if __name__ == "__main__":
    main()
