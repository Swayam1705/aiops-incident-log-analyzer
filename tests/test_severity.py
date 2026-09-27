"""Severity derivation tests: deterministic, volume/burst aware, never LLM-driven."""

from __future__ import annotations

import json

from app.analyzer.correlate import correlate
from app.analyzer.parser import parse_lines
from app.analyzer.severity import compute_severity, severity_meets_threshold
from app.log_generator import failure_record, normal_record


def _records_for(ground_truth: str, count: int):
    return parse_lines([json.dumps(failure_record(ground_truth, i)) for i in range(count)])


def test_invalid_configuration_is_critical():
    result = correlate(_records_for("invalid_configuration", 8), min_error_lines=5)
    decision = compute_severity(result)
    assert decision.severity == "critical"


def test_high_error_volume_escalates_severity():
    low = compute_severity(correlate(_records_for("database_timeout", 6), min_error_lines=5))
    high = compute_severity(correlate(_records_for("database_timeout", 30), min_error_lines=5))
    assert high.score >= low.score
    assert high.severity in {"high", "critical"}


def test_no_incident_is_info():
    records = parse_lines([json.dumps(normal_record(i)) for i in range(20)])
    decision = compute_severity(correlate(records, min_error_lines=5))
    assert decision.severity == "info"
    assert decision.score == 0


def test_burst_increases_score():
    without_burst = correlate(_records_for("http_500_burst", 5), min_error_lines=5)
    decision = compute_severity(without_burst)
    assert any("burst" in f for f in decision.factors)


def test_severity_threshold_helper():
    assert severity_meets_threshold("critical", "low") is True
    assert severity_meets_threshold("low", "high") is False
    assert severity_meets_threshold("info", "low") is False


def test_severity_has_factors_for_auditability():
    decision = compute_severity(correlate(_records_for("dependency_timeout", 10), min_error_lines=5))
    assert decision.factors  # non-empty, explains the score
