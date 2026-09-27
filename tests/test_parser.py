"""Parser tests: valid logs, malformed logs, noise, severity, pattern extraction."""

from __future__ import annotations

import json

from app.analyzer.parser import parse_line, parse_lines
from app.log_generator import failure_record, non_json_noise_line, normal_record


def test_parses_valid_json_record():
    record = failure_record("database_timeout", 0)
    parsed = parse_line(json.dumps(record))
    assert parsed.parsed is True
    assert parsed.severity in {"ERROR", "CRITICAL", "WARNING"}
    assert parsed.error_pattern == "database_timeout"
    assert parsed.service == "checkout-service"
    assert parsed.request_id is not None
    assert parsed.classification == "error"


def test_extracts_all_required_fields():
    line = json.dumps(
        {
            "timestamp": "2026-01-01T00:00:00.000Z",
            "level": "ERROR",
            "service": "checkout-service",
            "request_id": "req-abc",
            "event": "db_query_timeout",
            "message": "database query timed out after 5000ms",
            "error": {"type": "database_timeout", "code": "57014"},
            "status": 503,
        }
    )
    parsed = parse_line(line)
    assert parsed.timestamp == "2026-01-01T00:00:00.000Z"
    assert parsed.service == "checkout-service"
    assert parsed.severity == "ERROR"
    assert parsed.request_id == "req-abc"
    assert parsed.status == 503
    assert parsed.error_type == "database_timeout"


def test_malformed_json_does_not_raise():
    for bad in ["{ this is not json", '{"a":1', "", "   ", "random text"]:
        parsed = parse_line(bad)
        assert parsed.parsed is False
        assert parsed.classification == "noise"


def test_one_bad_line_does_not_break_batch():
    lines = [
        json.dumps(normal_record(1)),
        non_json_noise_line(0),
        "{ broken",
        json.dumps(failure_record("http_500_burst", 1)),
    ]
    records = parse_lines(lines)
    assert len(records) == 4
    assert sum(1 for r in records if r.parsed) == 2
    assert sum(1 for r in records if not r.parsed) == 2


def test_noise_line_classification():
    parsed = parse_line(non_json_noise_line(0))
    assert parsed.parsed is False
    assert parsed.classification == "noise"


def test_severity_normalization():
    assert parse_line(json.dumps({"level": "warn", "message": "x"})).severity == "WARNING"
    assert parse_line(json.dumps({"level": "err", "message": "x"})).severity == "ERROR"
    assert parse_line(json.dumps({"level": "critical", "message": "x"})).severity == "CRITICAL"


def test_pattern_detection_from_message_without_type():
    parsed = parse_line(json.dumps({"level": "ERROR", "message": "memory allocation failed, heap growing"}))
    assert parsed.error_pattern == "memory_pressure"


def test_typed_error_takes_precedence():
    parsed = parse_line(
        json.dumps(
            {
                "level": "ERROR",
                "message": "something odd happened",
                "error": {"type": "dependency_timeout"},
            }
        )
    )
    assert parsed.error_pattern == "dependency_timeout"


def test_evidence_rendering_is_single_line_and_bounded():
    long_message = "x" * 500
    parsed = parse_line(json.dumps({"level": "ERROR", "message": long_message, "service": "s"}))
    evidence = parsed.to_evidence(max_chars=80)
    assert "\n" not in evidence
    # body is truncated to max_chars with an ellipsis; header adds a bounded prefix
    assert "..." in evidence
    assert len(evidence) < 200


def test_numeric_signal_extraction():
    parsed = parse_line(json.dumps(failure_record("memory_pressure", 1)))
    assert any("rss" in k or "bytes" in k for k in parsed.numeric)
