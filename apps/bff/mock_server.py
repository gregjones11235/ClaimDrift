import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol, Optional
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from ingestion.common.elastic import ElasticsearchHttpClient


SEED_DIR = ROOT / "elastic" / "demo_seed"
PORT = int(os.getenv("BFF_PORT", "8787"))
INCLUDE_DEMO_RECORDS = os.getenv("BFF_INCLUDE_DEMO", "").lower() in {"1", "true", "yes"}


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

    def patterns(self) -> list[dict]:
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

    def patterns(self) -> list[dict]:
        return load_rows("drift_patterns")


class ElasticDataSource:
    mode = "elastic"

    def __init__(self) -> None:
        self.client = ElasticsearchHttpClient()

    def visible_query(self, query: dict) -> dict:
        if INCLUDE_DEMO_RECORDS:
            return query
        return {
            "bool": {
                "must": [query],
                "must_not": [{"term": {"record_source": "demo_seed"}}],
            }
        }

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

    def patterns(self) -> list[dict]:
        return self.search(
            "drift_patterns",
            {
                "query": self.visible_query({"match_all": {}}),
                "size": 100,
                "sort": [{"support_count": {"order": "desc", "unmapped_type": "long"}}],
            },
        )


def claim_ids_for_event(event: dict) -> set[str]:
    claim_ids = set()
    for diff in event.get("claim_diffs", []):
        if diff.get("preprint_claim_id"):
            claim_ids.add(diff["preprint_claim_id"])
        if diff.get("published_claim_id"):
            claim_ids.add(diff["published_claim_id"])
    return claim_ids


def build_data_source() -> DataSource:
    has_basic_auth = os.getenv("ELASTIC_USERNAME") and os.getenv("ELASTIC_PASSWORD")
    if os.getenv("ELASTIC_ENDPOINT") and (os.getenv("ELASTIC_API_KEY") or has_basic_auth):
        return ElasticDataSource()
    return SeedDataSource()


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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
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

            if path == "/api/drift-events":
                events = DATA_SOURCE.drift_events()
                send_json(self, 200, {"items": events, "count": len(events)})
                return

            if path.startswith("/api/drift-events/"):
                parts = path.split("/")
                event_id = parts[3]
                suffix = parts[4] if len(parts) > 4 else None
                self.handle_drift_event(event_id, suffix)
                return

            if path == "/api/patterns":
                rows = DATA_SOURCE.patterns()
                send_json(self, 200, {"items": rows, "count": len(rows)})
                return

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

        send_json(self, 404, {"error": "unknown_drift_event_view"})

    def stream_events(self, query: dict[str, list[str]]) -> None:
        drift_event_id = query.get("drift_event_id", ["demo-drift-001"])[0]
        events = [
            ("agent.started", "claim_extractor", {"input_summary": "Extracting claims from final preprint and published version."}),
            ("agent.completed", "claim_extractor", {"output_summary": "2 claims extracted.", "output_id": "claims"}),
            ("agent.started", "drift_analyzer", {"input_summary": "Comparing claim sets and retrieving memory patterns."}),
            ("agent.pattern_retrieved", "drift_analyzer", {"pattern_ids": ["pattern-demo-001"], "similarity_scores": [0.84]}),
            ("agent.completed", "drift_analyzer", {"output_summary": "Numerical shift detected.", "output_id": drift_event_id}),
            ("agent.started", "citation_finder", {"input_summary": "Finding citing papers through OpenAlex edge data."}),
            ("agent.completed", "citation_finder", {"output_summary": "1 affected citation scored.", "output_id": "affected_citations"}),
            ("agent.started", "notifier", {"input_summary": "Drafting notification email."}),
            ("agent.completed", "notifier", {"output_summary": "1 notification drafted.", "output_id": "notification_log"})
        ]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            for idx, (event_type, agent_id, payload) in enumerate(events, start=1):
                body = {
                    "event_type": event_type,
                    "agent_id": agent_id,
                    "drift_event_id": drift_event_id,
                    "timestamp": now(),
                    "payload": payload
                }
                self.wfile.write(f"id: {idx}\n".encode("utf-8"))
                self.wfile.write(f"event: {event_type}\n".encode("utf-8"))
                self.wfile.write(f"data: {json.dumps(body)}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.8)

            heartbeat = {
                "event_type": "heartbeat",
                "agent_id": None,
                "drift_event_id": drift_event_id,
                "timestamp": now(),
                "payload": {}
            }
            self.wfile.write(f"event: heartbeat\ndata: {json.dumps(heartbeat)}\n\n".encode("utf-8"))
            self.wfile.flush()
        except BrokenPipeError:
            return

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    if not (SEED_DIR / "drift_events.json").exists():
        print("Demo seed data is missing. Run: python3 elastic/scripts/seed_demo_cases.py")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Mock BFF running at http://127.0.0.1:{PORT} ({DATA_SOURCE.mode} data source)")
    server.serve_forever()


if __name__ == "__main__":
    main()
