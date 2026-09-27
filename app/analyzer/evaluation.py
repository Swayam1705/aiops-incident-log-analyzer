"""Ground-truth evaluation harness.

Runs each of the five known failure scenarios (plus a normal-traffic control)
through the *real* pipeline and compares the analyzer's output against the fixed
ground-truth label. Accuracy is reported honestly:

* a scenario is ``match`` only when the detected cause equals the ground truth
* ``uncertain`` when the analyzer abstains (unknown / insufficient evidence)
* ``false_positive`` when the normal-traffic control produces an incident
* the LLM column is reported separately and may legitimately be lower or abstain

Ground truth is never derived from analyzer output, and results are never adjusted
to look better.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analyzer import parser as parser_mod
from app.analyzer.pipeline import Analyzer
from app.faults import EVALUATION_SCENARIOS, FAULT_SPECS
from app.log_generator import non_json_noise_line, normal_log_lines, scenario_log_lines

# Human-readable labels for the evaluation table.
SCENARIO_LABELS = {
    "database-timeout": "Database timeout",
    "memory-pressure": "Memory pressure",
    "http-500-burst": "HTTP 500 burst",
    "dependency-timeout": "Dependency timeout",
    "invalid-configuration": "Invalid config",
}

# Extra scenario exercising "no incident expected".
NORMAL_CONTROL = "__normal__"


@dataclass
class ScenarioEvaluation:
    scenario: str
    label: str
    expected_cause: str | None
    rule_detected: str | None
    rule_confidence: float
    llm_status: str
    llm_detected: str | None
    llm_confidence: float | None
    llm_agreed: bool | None
    evidence_count: int
    noise_ignored: int
    outcome: str  # match | mismatch | uncertain | true_negative | false_positive
    evidence_correct: bool
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "label": self.label,
            "expected_cause": self.expected_cause,
            "rule_detected": self.rule_detected,
            "rule_confidence": self.rule_confidence,
            "llm_status": self.llm_status,
            "llm_detected": self.llm_detected,
            "llm_confidence": self.llm_confidence,
            "llm_agreed": self.llm_agreed,
            "evidence_count": self.evidence_count,
            "noise_ignored": self.noise_ignored,
            "outcome": self.outcome,
            "evidence_correct": self.evidence_correct,
            "notes": self.notes,
        }


@dataclass
class EvaluationReport:
    scenarios: list[ScenarioEvaluation] = field(default_factory=list)
    llm_enabled: bool = False
    llm_model: str | None = None
    llm_available: bool = False
    window_size: int = 0

    @property
    def rule_matches(self) -> int:
        return sum(1 for s in self.scenarios if s.outcome == "match")

    @property
    def llm_matches(self) -> int:
        return sum(
            1
            for s in self.scenarios
            if s.expected_cause and s.llm_detected == s.expected_cause
        )

    @property
    def scored_scenarios(self) -> int:
        return sum(1 for s in self.scenarios if s.expected_cause is not None)

    @property
    def false_positives(self) -> int:
        return sum(1 for s in self.scenarios if s.outcome == "false_positive")

    def rule_accuracy(self) -> str:
        scored = self.scored_scenarios
        return f"{self.rule_matches}/{scored}" if scored else "n/a"

    def llm_accuracy(self) -> str:
        scored = self.scored_scenarios
        if not self.llm_available:
            return "n/a (llm unavailable)"
        return f"{self.llm_matches}/{scored}" if scored else "n/a"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenarios": [s.to_dict() for s in self.scenarios],
            "rule_accuracy": self.rule_accuracy(),
            "llm_accuracy": self.llm_accuracy(),
            "rule_matches": self.rule_matches,
            "llm_matches": self.llm_matches,
            "scored_scenarios": self.scored_scenarios,
            "false_positives": self.false_positives,
            "llm_enabled": self.llm_enabled,
            "llm_model": self.llm_model,
            "llm_available": self.llm_available,
            "window_size": self.window_size,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Evaluation Report",
            "",
            f"- Rule-based accuracy: **{self.rule_accuracy()}**",
            f"- LLM-assisted accuracy: **{self.llm_accuracy()}**",
            f"- LLM available during run: `{self.llm_available}` (model: `{self.llm_model or 'n/a'}`)",
            f"- Log window size: `{self.window_size}`",
            "",
            "## Results",
            "",
            "| Scenario | Expected | Rule detected | Rule conf. | LLM status | LLM detected | Outcome |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for s in self.scenarios:
            lines.append(
                "| {label} | {exp} | {rd} | {rc:.2f} | {ls} | {ld} | {out} |".format(
                    label=s.label,
                    exp=s.expected_cause or "-",
                    rd=s.rule_detected or "unknown",
                    rc=s.rule_confidence,
                    ls=s.llm_status,
                    ld=s.llm_detected or "-",
                    out=s.outcome,
                )
            )
        lines += [
            "",
            "_Ground truth is fixed by the specification and is never altered to match",
            "analyzer output. 'unknown'/insufficient-evidence outcomes are reported as-is._",
        ]
        return "\n".join(lines) + "\n"

    def to_ascii_table(self) -> str:
        header = (
            f"{'Scenario':<20}{'Expected':<22}{'Detected':<22}{'Result':<16}"
        )
        rows = [
            "Evaluation results",
            "=" * len(header),
            header,
            "-" * len(header),
        ]
        for s in self.scenarios:
            rows.append(
                f"{s.label:<20}{(s.expected_cause or '-'):<22}"
                f"{(s.rule_detected or 'unknown'):<22}{s.outcome:<16}"
            )
        rows.append("-" * len(header))
        rows.append(f"Rule-based accuracy: {self.rule_accuracy()}    LLM accuracy: {self.llm_accuracy()}")
        return "\n".join(rows)


def _generate_window(scenario: str, window_size: int, *, with_noise: bool = True) -> list[str]:
    if scenario == NORMAL_CONTROL:
        lines = normal_log_lines(total=window_size)
    else:
        lines = scenario_log_lines(scenario, total=window_size)
    if with_noise:
        # Sprinkle unparseable lines to prove noise handling end-to-end.
        for index in range(0, len(lines), 23):
            lines.insert(index, non_json_noise_line(index))
    return lines[-window_size:]


def run_evaluation(
    analyzer: Analyzer,
    window_size: int = 200,
    llm_available: bool | None = None,
) -> EvaluationReport:
    """Run all five scenarios + a normal control through the real pipeline."""
    report = EvaluationReport(window_size=window_size)
    report.llm_enabled = analyzer.settings.llm_requested
    report.llm_model = analyzer.settings.ollama_model if report.llm_enabled else None

    # Probe LLM availability once (if configured) so the report is honest about it.
    if analyzer.llm is not None and report.llm_enabled:
        available, _ = analyzer.llm.available()
        report.llm_available = bool(available) if llm_available is None else llm_available
    else:
        report.llm_available = False

    scenarios = [*EVALUATION_SCENARIOS, NORMAL_CONTROL]
    for scenario in scenarios:
        lines = _generate_window(scenario, window_size)
        is_control = scenario == NORMAL_CONTROL
        expected = None if is_control else FAULT_SPECS[scenario].ground_truth
        label = "Normal traffic" if is_control else SCENARIO_LABELS[scenario]

        outcome = analyzer.analyze_lines(
            lines,
            ground_truth=expected,
            trigger="evaluation",
        )
        correlation = outcome.correlation
        rule_detected = correlation.suspected_root_cause
        llm = outcome.llm

        # Evidence correctness: for a scored scenario, the dominant correlated
        # pattern must equal the ground truth; for the control, there must be none.
        if is_control:
            evidence_correct = not correlation.has_incident
            result_outcome = "false_positive" if correlation.has_incident else "true_negative"
        else:
            evidence_correct = rule_detected == expected
            if rule_detected == expected:
                result_outcome = "match"
            elif rule_detected is None:
                result_outcome = "uncertain"
            else:
                result_outcome = "mismatch"

        notes = ""
        if not is_control and correlation.error_lines == 0:
            notes = "no error lines correlated in window"
        elif is_control and correlation.error_lines:
            notes = f"{correlation.error_lines} stray error line(s) in normal control"

        report.scenarios.append(
            ScenarioEvaluation(
                scenario=scenario,
                label=label,
                expected_cause=expected,
                rule_detected=rule_detected,
                rule_confidence=correlation.rule_confidence,
                llm_status=llm.status,
                llm_detected=llm.suspected_root_cause,
                llm_confidence=llm.confidence,
                llm_agreed=analyzer._agreement(rule_detected, llm),  # noqa: SLF001
                evidence_count=len(correlation.evidence_records),
                noise_ignored=correlation.noise_lines,
                outcome=result_outcome,
                evidence_correct=evidence_correct,
                notes=notes,
            )
        )
    return report


def parse_records_count(lines: list[str]) -> tuple[int, int]:
    """(parsed, noise) counts for a set of raw lines - used by tests/docs."""
    records = parser_mod.parse_lines(lines)
    parsed = sum(1 for r in records if r.parsed)
    return parsed, len(records) - parsed
