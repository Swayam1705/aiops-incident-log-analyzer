"""Report tests: JSON/Markdown generation, persistence, validation, retention."""

from __future__ import annotations

import json

from app.analyzer.correlate import correlate
from app.analyzer.parser import parse_lines
from app.analyzer.report import AIAnalysis, ReportStore, RuleAnalysis, build_report, new_incident_id
from app.analyzer.severity import compute_severity
from app.log_generator import scenario_log_lines


def _make_incident(store, scenario="database-timeout"):
    lines = scenario_log_lines(scenario, total=120)
    correlation = correlate(parse_lines(lines), min_error_lines=5)
    severity = compute_severity(correlation)
    rule = RuleAnalysis(
        suspected_root_cause=correlation.suspected_root_cause,
        confidence=correlation.rule_confidence,
        evidence_lines=correlation.evidence_lines(limit=5),
        burst_detected=correlation.burst_detected,
        burst_size=correlation.burst_size,
        error_lines=correlation.error_lines,
        noise_lines=correlation.noise_lines,
        reasons=correlation.reasons,
    )
    ai = AIAnalysis(
        available=False, model=None, summary=None, suspected_root_cause=None,
        evidence_lines=[], confidence=None, recommended_action=None, status="disabled",
    )
    report = build_report(
        service="checkout-service",
        severity=severity.severity,
        severity_decision=severity.to_dict(),
        summary="test incident",
        rule_analysis=rule,
        ai_analysis=ai,
        evidence_lines=correlation.evidence_lines(limit=5),
        recommended_action="inspect database connectivity (advisory)",
        window={"size": len(lines)},
        ground_truth="database_timeout",
        runbook_id="database_timeout",
    )
    store.save(report)
    return report


def test_report_has_required_fields():
    report = _make_incident(ReportStore("/tmp/_rt1", retention_limit=10))
    data = report.to_dict()
    for field in [
        "incident_id", "timestamp", "service", "severity", "summary",
        "suspected_root_cause", "evidence_lines", "confidence",
        "recommended_action", "analysis_source",
    ]:
        assert field in data


def test_json_and_markdown_written(store):
    report = _make_incident(store)
    assert (store.directory / f"{report.incident_id}.json").exists()
    assert (store.directory / f"{report.incident_id}.md").exists()


def test_markdown_contains_sections(store):
    report = _make_incident(store)
    md = store.get_markdown(report.incident_id)
    assert "# Incident" in md
    assert "## Evidence lines" in md
    assert "AI-assisted analysis" in md
    assert "advisory" in md.lower()


def test_rule_based_source_when_llm_unavailable(store):
    report = _make_incident(store)
    assert report.analysis_source == "rule-based"


def test_validation_roundtrip(store):
    report = _make_incident(store)
    updated = store.set_validation(report.incident_id, "confirmed", validated_by="tester", note="looks right")
    assert updated["validation"]["status"] == "confirmed"
    assert updated["validation"]["validated_by"] == "tester"
    # markdown re-rendered with the decision
    assert "confirmed" in store.get_markdown(report.incident_id)


def test_invalid_validation_status_rejected(store):
    report = _make_incident(store)
    try:
        store.set_validation(report.incident_id, "bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_retention_prunes_oldest(tmp_path):
    store = ReportStore(tmp_path / "inc", retention_limit=3)
    for i in range(6):
        report = _make_incident(store)
        report.incident_id = f"inc-test-{i}"
        store.save(report)
    assert len(list(store.directory.glob("*.json"))) <= 3


def test_list_returns_summaries(store):
    _make_incident(store)
    summaries = store.list()
    assert summaries
    assert "incident_id" in summaries[0]
    assert "severity" in summaries[0]


def test_no_secrets_in_report(store):
    report = _make_incident(store)
    blob = json.dumps(report.to_dict()).lower()
    for token in ["password", "admin_token", "api_key"]:
        assert token not in blob


def test_incident_id_unique():
    assert new_incident_id() != new_incident_id()
