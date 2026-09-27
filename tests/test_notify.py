"""Notification tests: best-effort delivery, graceful failure, no secret leakage."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.analyzer.notify import Notifier, build_payload


class _Handler(BaseHTTPRequestHandler):
    received = None
    status = 200

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _Handler.received = self.rfile.read(length).decode()
        self.send_response(_Handler.status)
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture()
def webhook_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


def _incident():
    return {
        "incident_id": "inc-test",
        "timestamp": "2026-01-01T00:00:00Z",
        "service": "checkout-service",
        "severity": "high",
        "summary": "database timeouts detected",
        "suspected_root_cause": "database_timeout",
        "confidence": 0.9,
        "analysis_source": "rule-based",
        "recommended_action": "inspect db",
        "evidence_lines": ["line1", "line2"],
    }


def test_unconfigured_notifier_skips():
    result = Notifier("").send(_incident())
    assert result.delivered is False
    assert "no webhook" in (result.detail or "")


def test_successful_delivery(webhook_server):
    _Handler.status = 200
    host, port = webhook_server.server_address
    result = Notifier(f"http://{host}:{port}").send(_incident())
    assert result.delivered is True
    payload = json.loads(_Handler.received)
    assert payload["incident_id"] == "inc-test"
    assert payload["severity"] == "high"
    assert payload["requires_human_validation"] is True


def test_failed_delivery_does_not_raise(webhook_server):
    _Handler.status = 500
    host, port = webhook_server.server_address
    result = Notifier(f"http://{host}:{port}").send(_incident())
    assert result.delivered is False


def test_unreachable_webhook_does_not_raise():
    result = Notifier("http://127.0.0.1:59998", timeout_seconds=1).send(_incident())
    assert result.delivered is False


def test_payload_contains_no_secrets():
    blob = json.dumps(build_payload(_incident())).lower()
    for token in ["password", "token", "api_key"]:
        assert token not in blob


def test_token_in_url_is_redacted_from_detail():
    result = Notifier("http://127.0.0.1:59998/?token=supersecret", timeout_seconds=1).send(_incident())
    assert "supersecret" not in (result.detail or "")
