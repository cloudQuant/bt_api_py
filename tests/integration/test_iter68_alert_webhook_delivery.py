"""Real local HTTP Webhook delivery for one Monitor alert lifecycle event."""
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from bt_api_monitor.alert_delivery import (
    AlertDeliveryService,
    AlertLifecycleEvent,
    WebhookAlertWorker,
    WebhookSinkConfig,
)
from bt_api_monitor.durable import DurableOutbox


class _Receiver(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).received.append((self.path, body, self.headers.get("Content-Type")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *_args):
        pass


def test_alert_webhook_is_really_posted_and_delivered(tmp_path):
    _Receiver.received = []
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        outbox = DurableOutbox(tmp_path / "alerts.sqlite")
        sink = WebhookSinkConfig(
            "local-webhook", 1, enabled=True, url=f"http://127.0.0.1:{port}/alert",
            timeout_seconds=2.0, max_attempts=2, backoff_initial_seconds=0.05,
            backoff_max_seconds=0.1, total_duration_seconds=10.0,
        )
        service = AlertDeliveryService(outbox, sinks=(sink,))
        service.record(AlertLifecycleEvent(
            event_id="alert-real-1", lifecycle_key="lifecycle-real-1", state="TRIGGERED",
            occurred_at=time.time(), rule_id="recovery-duration", rule_version=1,
            scope="events", event_type="market.recovery", dimensions=(("venue", "SHFE"),),
        ))
        assert WebhookAlertWorker(service, poll_interval_seconds=0.05).run_once() == 1
        assert len(_Receiver.received) == 1
        assert _Receiver.received[0][0] == "/alert"
        assert _Receiver.received[0][2] == "application/json"
        status = service.status()
        assert status.sinks["local-webhook"].delivered == 1
        assert status.sinks["local-webhook"].pending == 0
    finally:
        server.shutdown()
