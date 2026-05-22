import json
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "elastic" / "demo_seed"
PORT = 8787


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_rows(name: str) -> list[dict]:
    path = SEED_DIR / f"{name}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


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
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/events/stream":
            self.stream_events(parse_qs(parsed.query))
            return

        if path == "/api/drift-events":
            events = load_rows("drift_events")
            send_json(self, 200, {"items": events, "count": len(events)})
            return

        if path.startswith("/api/drift-events/"):
            parts = path.split("/")
            event_id = parts[3]
            suffix = parts[4] if len(parts) > 4 else None
            self.handle_drift_event(event_id, suffix)
            return

        if path == "/api/patterns":
            rows = load_rows("drift_patterns")
            send_json(self, 200, {"items": rows, "count": len(rows)})
            return

        send_json(self, 404, {"error": "not_found"})

    def handle_drift_event(self, event_id: str, suffix: Optional[str]) -> None:
        events = load_rows("drift_events")
        event = next((row for row in events if row["event_id"] == event_id), None)
        if event is None:
            send_json(self, 404, {"error": "drift_event_not_found"})
            return

        if suffix is None:
            send_json(self, 200, event)
            return

        if suffix == "claims":
            claim_ids = {
                diff["preprint_claim_id"]
                for diff in event.get("claim_diffs", [])
            } | {
                diff["published_claim_id"]
                for diff in event.get("claim_diffs", [])
            }
            rows = [row for row in load_rows("claims") if row["claim_id"] in claim_ids]
            send_json(self, 200, {"items": rows, "count": len(rows)})
            return

        if suffix == "affected-citations":
            rows = [row for row in load_rows("affected_citations") if row["drift_event_id"] == event_id]
            send_json(self, 200, {"items": rows, "count": len(rows)})
            return

        if suffix == "notifications":
            rows = [row for row in load_rows("notification_log") if row["drift_event_id"] == event_id]
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
    print(f"Mock BFF running at http://127.0.0.1:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
