"""Correlation tests: bursts, dominant pattern, noise separation, five scenarios."""

from __future__ import annotations

import json

import pytest

from app.analyzer.correlate import correlate, is_noisy_but_harmless
from app.analyzer.parser import parse_lines
from app.faults import EVALUATION_SCENARIOS, FAULT_SPECS
from app.log_generator import failure_record, normal_log_lines, normal_record, scenario_log_lines


def _parse(records):
    return parse_lines([json.dumps(r) for r in records])


@pytest.mark.parametrize("scenario", list(EVALUATION_SCENARIOS))
def test_detects_each_known_failure(scenario):
    lines = scenario_log_lines(scenario, total=160)
    result = correlate(parse_lines(lines), min_error_lines=5)
    expected = FAULT_SPECS[scenario].ground_truth
    assert result.suspected_root_cause == expected
    assert result.burst_detected is True
    assert result.rule_confidence > 0.6


def test_burst_detection_groups_same_pattern():
    records = _parse([failure_record("database_timeout", i) for i in range(10)])
    result = correlate(records, min_error_lines=5)
    assert result.burst_size >= 2
    assert result.pattern_counts["database_timeout"] >= 5


def test_noise_does_not_create_false_incident():
    lines = normal_log_lines(total=120)
    result = correlate(parse_lines(lines), min_error_lines=5)
    assert result.suspected_root_cause is None
    assert is_noisy_but_harmless(result) is True


def test_below_threshold_reports_unknown():
    records = _parse([failure_record("http_500_burst", i) for i in range(2)])
    result = correlate(records, min_error_lines=5)
    assert result.suspected_root_cause is None
    assert result.error_lines == 2


def test_unparseable_lines_counted_as_noise():
    lines = [json.dumps(failure_record("memory_pressure", i)) for i in range(8)]
    lines += ["not json", "{ broken", ""]
    result = correlate(parse_lines(lines), min_error_lines=5)
    assert result.noise_lines == 3
    assert result.suspected_root_cause == "memory_pressure"


def test_evidence_lines_are_real_log_content():
    lines = scenario_log_lines("dependency-timeout", total=120)
    result = correlate(parse_lines(lines), min_error_lines=5)
    evidence = result.evidence_lines(limit=5)
    assert evidence
    assert any("payment-gateway" in e or "dependency" in e.lower() for e in evidence)


def test_normal_traffic_no_error_lines():
    records = _parse([normal_record(i) for i in range(30)])
    result = correlate(records, min_error_lines=5)
    assert result.error_lines == 0
    assert result.has_incident is False
