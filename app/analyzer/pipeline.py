"""The analysis pipeline orchestrator.

Order of operations (the deterministic analysis always runs first and always
produces a usable report):

    collect -> parse -> correlate -> severity -> runbook
        -> (optional) LLM advisory -> build report -> store -> notify

Failure isolation: a malformed line, an unavailable model, a failed notification or
an unwritable report is caught locally and recorded - it never aborts the run or
crashes the caller. The rule-based report survives every downstream failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.analyzer import collector as collector_mod
from app.analyzer import llm_client as llm_mod
from app.analyzer import notify as notify_mod
from app.analyzer import parser as parser_mod
from app.analyzer import report as report_mod
from app.analyzer import runbook as runbook_mod
from app.analyzer import severity as severity_mod
from app.analyzer.correlate import CorrelationResult
from app.analyzer.correlate import correlate as correlate_records
from app.config import Settings

logger = logging.getLogger("app.analyzer")


@dataclass
class AnalysisOutcome:
    """Result of one analysis run."""

    incident: report_mod.IncidentReport | None
    correlation: CorrelationResult
    severity: severity_mod.SeverityDecision
    llm: llm_mod.LLMResult
    notification: notify_mod.NotificationResult | None
    stored_paths: list[str] = field(default_factory=list)
    incident_created: bool = False
    errors: list[str] = field(default_factory=list)
    window_size: int = 0
    trigger: str = "manual"

    def summary_dict(self) -> dict[str, Any]:
        return {
            "incident_created": self.incident_created,
            "incident_id": self.incident.incident_id if self.incident else None,
            "suspected_root_cause": self.incident.suspected_root_cause if self.incident else None,
            "severity": self.severity.severity,
            "analysis_source": self.incident.analysis_source if self.incident else "rule-based",
            "error_lines": self.correlation.error_lines,
            "noise_lines": self.correlation.noise_lines,
            "burst_detected": self.correlation.burst_detected,
            "llm_status": self.llm.status,
            "llm_available": self.llm.available,
            "notification": self.notification.to_dict() if self.notification else None,
            "stored_paths": self.stored_paths,
            "errors": self.errors,
            "window_size": self.window_size,
            "trigger": self.trigger,
        }


class Analyzer:
    """Stateless-ish analyzer wired to settings, store, LLM client and notifier."""

    def __init__(
        self,
        settings: Settings,
        store: report_mod.ReportStore,
        llm: llm_mod.OllamaClient | None = None,
        notifier: notify_mod.Notifier | None = None,
        metrics: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        self.notifier = notifier
        self.metrics = metrics

    # -- helpers ---------------------------------------------------------
    def _observe_llm(self, status: str) -> None:
        if self.metrics is not None:
            try:
                self.metrics.llm_calls_total.inc(outcome=status)
            except Exception:  # pragma: no cover - metrics must never break analysis
                pass

    def _observe_notification(self, delivered: bool) -> None:
        if self.metrics is not None:
            try:
                self.metrics.notifications_total.inc(result="sent" if delivered else "skipped_or_failed")
            except Exception:  # pragma: no cover
                pass

    # -- core ------------------------------------------------------------
    def analyze_lines(
        self,
        lines: list[str],
        *,
        service: str | None = None,
        ground_truth: str | None = None,
        trigger: str = "manual",
        force_incident: bool = False,
        source: str = "window",
    ) -> AnalysisOutcome:
        """Analyze an in-memory bounded window of raw log lines."""
        records = parser_mod.parse_lines(lines, source=source)
        return self._analyze_records(
            records,
            service=service,
            ground_truth=ground_truth,
            trigger=trigger,
            force_incident=force_incident,
            window_size=len(lines),
        )

    def analyze_files(
        self,
        paths: list[Path] | None = None,
        *,
        service: str | None = None,
        ground_truth: str | None = None,
        trigger: str = "scheduled",
        force_incident: bool = False,
    ) -> AnalysisOutcome:
        """Collect a bounded window from configured log files and analyze it."""
        target_paths = paths if paths is not None else self.settings.log_files()
        collection = collector_mod.collect_lines(
            target_paths,
            window_size=self.settings.log_window_size,
            max_bytes_per_file=self.settings.collector_max_bytes,
        )
        records = parser_mod.parse_lines(collection.lines, source="file")
        return self._analyze_records(
            records,
            service=service,
            ground_truth=ground_truth,
            trigger=trigger,
            force_incident=force_incident,
            window_size=len(collection.lines),
        )

    def _analyze_records(
        self,
        records: list[parser_mod.ParsedRecord],
        *,
        service: str | None,
        ground_truth: str | None,
        trigger: str,
        force_incident: bool,
        window_size: int,
    ) -> AnalysisOutcome:
        errors: list[str] = []

        # 1) Deterministic correlation - always runs, never depends on the LLM.
        correlation = correlate_records(records, min_error_lines=self.settings.analyze_min_error_lines)
        severity_decision = severity_mod.compute_severity(correlation)

        if self.metrics is not None:
            try:
                self.metrics.log_lines_collected.set(correlation.total_lines)
                self.metrics.log_lines_parsed.set(correlation.parsed_lines)
                self.metrics.log_parse_errors.set(correlation.noise_lines)
            except Exception:  # pragma: no cover
                pass

        service_name = service or self.settings.app_name

        # 2) Optional LLM advisory stage - fully isolated.
        llm_result = self._run_llm(correlation, errors)

        # 3) Decide whether this window constitutes an incident.
        incident_signal = correlation.has_incident or (force_incident and correlation.error_lines > 0)
        if not incident_signal:
            # No incident: still record an analysis run, but create no report.
            self._observe_analysis(trigger, "no_incident")
            return AnalysisOutcome(
                incident=None,
                correlation=correlation,
                severity=severity_decision,
                llm=llm_result,
                notification=None,
                window_size=window_size,
                trigger=trigger,
                errors=errors,
            )

        # 4) Build the incident report.
        runbook = runbook_mod.get_runbook(correlation.suspected_root_cause or "unknown")
        recommended_action = runbook_mod.recommended_action_for(
            correlation.suspected_root_cause or "unknown"
        )
        summary = self._build_summary(correlation, severity_decision, service_name)
        evidence_lines = correlation.evidence_lines(limit=20)

        rule_analysis = report_mod.RuleAnalysis(
            suspected_root_cause=correlation.suspected_root_cause,
            confidence=correlation.rule_confidence,
            evidence_lines=evidence_lines,
            burst_detected=correlation.burst_detected,
            burst_size=correlation.burst_size,
            error_lines=correlation.error_lines,
            noise_lines=correlation.noise_lines,
            reasons=correlation.reasons,
            pattern_counts=correlation.pattern_counts,
            numeric_signals=correlation.numeric_signals,
        )
        ai_analysis = report_mod.AIAnalysis(
            available=llm_result.available,
            model=llm_result.model,
            summary=llm_result.summary,
            suspected_root_cause=llm_result.suspected_root_cause,
            evidence_lines=llm_result.evidence_lines or [],
            confidence=llm_result.confidence,
            recommended_action=llm_result.recommended_action,
            status=llm_result.status,
            error=llm_result.error,
            raw_excerpt=llm_result.raw_excerpt,
            agreed_with_rules=self._agreement(correlation.suspected_root_cause, llm_result),
        )

        report = report_mod.build_report(
            service=service_name,
            severity=severity_decision.severity,
            severity_decision=severity_decision.to_dict(),
            summary=summary,
            rule_analysis=rule_analysis,
            ai_analysis=ai_analysis,
            evidence_lines=evidence_lines,
            recommended_action=recommended_action,
            window={
                "size": window_size,
                "start": correlation.window_start,
                "end": correlation.window_end,
                "log_window_size": self.settings.log_window_size,
            },
            ground_truth=ground_truth,
            runbook_id=(runbook.runbook_id if runbook else None),
        )

        # 5) Persist (isolated) and update metrics.
        stored: list[str] = []
        try:
            json_path, md_path = self.store.save(report)
            stored = [str(json_path), str(md_path)]
        except OSError as exc:
            errors.append(f"failed to persist report: {exc}")
            logger.warning("incident persistence failed: %s", exc)

        if self.metrics is not None:
            try:
                self.metrics.incidents_total.inc(
                    severity=report.severity,
                    root_cause=report.suspected_root_cause or "unknown",
                    analysis_source=report.analysis_source,
                )
            except Exception:  # pragma: no cover
                pass

        # 6) Notify (isolated, best-effort).
        notification = None
        if self.notifier is not None and severity_mod.severity_meets_threshold(
            report.severity, "low"
        ):
            try:
                notification = self.notifier.send(report.to_dict())
                self._observe_notification(notification.delivered)
            except Exception as exc:  # pragma: no cover - notifier is already defensive
                errors.append(f"notification error: {exc}")

        self._observe_analysis(trigger, "incident_created")
        return AnalysisOutcome(
            incident=report,
            correlation=correlation,
            severity=severity_decision,
            llm=llm_result,
            notification=notification,
            stored_paths=stored,
            incident_created=True,
            errors=errors,
            window_size=window_size,
            trigger=trigger,
        )

    # -- sub-steps -------------------------------------------------------
    def _run_llm(
        self, correlation: CorrelationResult, errors: list[str]
    ) -> llm_mod.LLMResult:
        if self.llm is None or not self.settings.llm_requested:
            result = llm_mod.disabled_result("llm disabled or not configured")
            self._observe_llm(result.status)
            return result
        context = {
            "service": self.settings.app_name,
            "suspected_root_cause_rule_based": correlation.suspected_root_cause,
            "rule_confidence": correlation.rule_confidence,
            "error_lines": correlation.error_lines,
            "noise_lines": correlation.noise_lines,
            "burst_detected": correlation.burst_detected,
            "pattern_counts": correlation.pattern_counts,
            "numeric_signals": correlation.numeric_signals,
        }
        evidence = correlation.evidence_lines(limit=self.settings.llm_max_context_lines)
        try:
            result = self.llm.analyze(context, evidence)
        except Exception as exc:  # pragma: no cover - client is defensive; belt and braces
            errors.append(f"llm analysis raised unexpectedly: {exc}")
            result = llm_mod.LLMResult(
                available=False, status="unavailable", model=self.settings.ollama_model, error=str(exc)
            )
        self._observe_llm(result.status)
        return result

    def _observe_analysis(self, trigger: str, outcome: str) -> None:
        if self.metrics is not None:
            try:
                self.metrics.analysis_total.inc(trigger=trigger, outcome=outcome)
            except Exception:  # pragma: no cover
                pass

    @staticmethod
    def _agreement(rule_cause: str | None, llm: llm_mod.LLMResult) -> bool | None:
        if not llm.available or llm.suspected_root_cause is None:
            return None
        if rule_cause is None:
            return llm.suspected_root_cause in {"unknown", "insufficient_evidence"}
        return llm.suspected_root_cause == rule_cause

    @staticmethod
    def _build_summary(
        correlation: CorrelationResult,
        severity_decision: severity_mod.SeverityDecision,
        service: str,
    ) -> str:
        if correlation.suspected_root_cause:
            return (
                f"Detected {correlation.error_lines} correlated error line(s) on '{service}' "
                f"matching '{correlation.suspected_root_cause}' "
                f"(burst={correlation.burst_detected}, severity={severity_decision.severity})."
            )
        return (
            f"Detected {correlation.error_lines} error line(s) on '{service}' with no single "
            f"dominant pattern; insufficient evidence for a specific root cause."
        )
