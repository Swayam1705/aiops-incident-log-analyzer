"""LLM output validation + Ollama client resilience tests.

The Ollama client is exercised against a real local HTTP server (stdlib), so the
actual request/parse/validate code path runs - not a mock of the client itself.
Covers: malformed JSON, missing/invalid fields, out-of-range confidence, timeout,
unavailable server, model-not-found, and the unknown/insufficient-evidence path.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.analyzer.llm_client import (
    ALLOWED_CAUSES,
    OllamaClient,
    extract_json,
    validate_llm_output,
)

# --- pure validation ---------------------------------------------------------


def _valid_payload():
    return {
        "summary": "Database queries are timing out under load.",
        "suspected_root_cause": "database_timeout",
        "evidence_lines": ["[t] ERROR db query timed out"],
        "confidence": 0.82,
        "recommended_action": "Inspect database connectivity and pool settings.",
    }


def test_valid_output_passes():
    result, error = validate_llm_output(_valid_payload(), max_evidence=10, max_response_chars=2000)
    assert error is None
    assert result is not None
    assert result.status == "ok"
    assert result.suspected_root_cause == "database_timeout"
    assert result.confidence == 0.82


def test_missing_field_is_fatal():
    payload = _valid_payload()
    del payload["confidence"]
    result, error = validate_llm_output(payload, 10, 2000)
    assert result is None
    assert "confidence" in error


def test_invalid_confidence_range_rejected():
    payload = _valid_payload()
    payload["confidence"] = 1.7
    result, error = validate_llm_output(payload, 10, 2000)
    assert result is None
    assert "out of range" in error


def test_boolean_confidence_rejected():
    payload = _valid_payload()
    payload["confidence"] = True
    result, error = validate_llm_output(payload, 10, 2000)
    assert result is None


def test_unknown_cause_coerced_not_fabricated():
    payload = _valid_payload()
    payload["suspected_root_cause"] = "cosmic_radiation"
    result, _ = validate_llm_output(payload, 10, 2000)
    assert result.suspected_root_cause == "unknown"
    assert result.status == "insufficient_evidence"


def test_insufficient_evidence_status():
    payload = _valid_payload()
    payload["suspected_root_cause"] = "unknown"
    payload["confidence"] = 0.1
    result, _ = validate_llm_output(payload, 10, 2000)
    assert result.status == "insufficient_evidence"


def test_extra_fields_ignored_not_fatal():
    payload = _valid_payload()
    payload["unexpected"] = {"foo": "bar"}
    result, error = validate_llm_output(payload, 10, 2000)
    assert error is None
    assert result is not None


def test_evidence_bounded():
    payload = _valid_payload()
    payload["evidence_lines"] = ["line"] * 100
    result, _ = validate_llm_output(payload, max_evidence=5, max_response_chars=2000)
    assert len(result.evidence_lines) == 5


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('here you go: {"a": 2} hope that helps') == {"a": 2}
    assert extract_json("no json here") is None
    assert extract_json('{"a": 1,}') == {"a": 1}  # trailing comma tolerated


# --- live client against a local HTTP server ---------------------------------


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *args):  # silence
        pass

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # /api/tags
        if self.mode == "unavailable":
            self._send(500, b'{"error":"down"}')
            return
        if self.mode == "model_not_found":
            self._send(200, json.dumps({"models": [{"name": "other:latest"}]}).encode())
            return
        self._send(200, json.dumps({"models": [{"name": "qwen2.5:1.5b"}]}).encode())

    def do_POST(self):  # /api/generate
        if self.mode == "malformed":
            self._send(200, json.dumps({"response": "this is not json at all"}).encode())
            return
        if self.mode == "empty":
            self._send(200, json.dumps({"response": ""}).encode())
            return
        if self.mode == "bad_fields":
            self._send(200, json.dumps({"response": json.dumps({"summary": "x"})}).encode())
            return
        if self.mode == "timeout":
            import time as _t

            _t.sleep(2.0)
            self._send(200, json.dumps({"response": json.dumps(_valid_payload())}).encode())
            return
        self._send(200, json.dumps({"response": json.dumps(_valid_payload())}).encode())


@pytest.fixture()
def ollama_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


def _client(server, **kwargs):
    host, port = server.server_address
    return OllamaClient(base_url=f"http://{host}:{port}", model="qwen2.5:1.5b", **kwargs)


def test_client_ok(ollama_server):
    _Handler.mode = "ok"
    result = _client(ollama_server, timeout_seconds=5).analyze({}, ["evidence"])
    assert result.available is True
    assert result.status == "ok"
    assert result.suspected_root_cause == "database_timeout"


def test_client_malformed_json(ollama_server):
    _Handler.mode = "malformed"
    result = _client(ollama_server, timeout_seconds=5).analyze({}, ["evidence"])
    assert result.available is False
    assert result.status == "validation_error"


def test_client_empty_response(ollama_server):
    _Handler.mode = "empty"
    result = _client(ollama_server, timeout_seconds=5).analyze({}, ["evidence"])
    assert result.status == "validation_error"


def test_client_missing_fields(ollama_server):
    _Handler.mode = "bad_fields"
    result = _client(ollama_server, timeout_seconds=5).analyze({}, ["evidence"])
    assert result.status == "validation_error"
    assert "missing required fields" in (result.error or "")


def test_client_timeout(ollama_server):
    _Handler.mode = "timeout"
    result = _client(ollama_server, timeout_seconds=0.5).analyze({}, ["evidence"])
    assert result.available is False
    assert result.status == "timeout"


def test_client_unavailable_server():
    # Nothing listening on this port.
    client = OllamaClient(base_url="http://127.0.0.1:59999", model="x", timeout_seconds=1)
    available, reason = client.available()
    assert available is False
    assert reason


def test_client_model_not_found(ollama_server):
    _Handler.mode = "model_not_found"
    available, reason = _client(ollama_server, timeout_seconds=5).available()
    assert available is False
    assert "not found" in (reason or "")


def test_disabled_client_never_calls_network():
    client = OllamaClient(base_url="http://127.0.0.1:59999", model="x", enabled=False)
    result = client.analyze({}, ["evidence"])
    assert result.status == "disabled"
    assert result.available is False


def test_allowed_causes_include_uncertainty():
    assert "unknown" in ALLOWED_CAUSES
    assert "insufficient_evidence" in ALLOWED_CAUSES
