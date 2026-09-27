"""Evaluation harness tests: ground-truth scoring and honest accuracy reporting."""

from __future__ import annotations

from app.analyzer.evaluation import run_evaluation


def test_rule_based_scores_all_five(analyzer):
    report = run_evaluation(analyzer, window_size=160, llm_available=False)
    assert report.scored_scenarios == 5
    assert report.rule_matches == 5
    assert report.rule_accuracy() == "5/5"


def test_normal_traffic_is_true_negative(analyzer):
    report = run_evaluation(analyzer, window_size=160, llm_available=False)
    control = next(s for s in report.scenarios if s.expected_cause is None)
    assert control.outcome == "true_negative"
    assert report.false_positives == 0


def test_llm_accuracy_reports_unavailable_honestly(analyzer):
    report = run_evaluation(analyzer, window_size=160, llm_available=False)
    assert "unavailable" in report.llm_accuracy()


def test_each_expected_cause_detected(analyzer):
    report = run_evaluation(analyzer, window_size=160, llm_available=False)
    by_scenario = {s.scenario: s for s in report.scenarios}
    assert by_scenario["database-timeout"].rule_detected == "database_timeout"
    assert by_scenario["memory-pressure"].rule_detected == "memory_pressure"
    assert by_scenario["http-500-burst"].rule_detected == "http_500_burst"
    assert by_scenario["dependency-timeout"].rule_detected == "dependency_timeout"
    assert by_scenario["invalid-configuration"].rule_detected == "invalid_configuration"


def test_evidence_correctness_flagged(analyzer):
    report = run_evaluation(analyzer, window_size=160, llm_available=False)
    scored = [s for s in report.scenarios if s.expected_cause]
    assert all(s.evidence_correct for s in scored)


def test_markdown_and_ascii_render(analyzer):
    report = run_evaluation(analyzer, window_size=120, llm_available=False)
    md = report.to_markdown()
    assert "Evaluation Report" in md
    assert "Rule-based accuracy" in md
    ascii_table = report.to_ascii_table()
    assert "Database timeout" in ascii_table
