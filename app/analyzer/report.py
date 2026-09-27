"""Incident report model, JSON/Markdown rendering, and bounded local storage.

An :class:`IncidentReport` is the normalized artifact the whole pipeline produces.
It clearly separates:

* ``analysis_source`` -> ``rule-based`` or ``llm-assisted``
* ``ground_truth``    -> the injected scenario label (evaluation mode only)
* ``validation``      -> the human-in-the-loop decision (pending by default)

The LLM's contribution is stored under ``ai_analysis`` and is explicitly marked
advisory and unvalidated. It never overwrites the deterministic fields.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.logging_config import utc_now_iso

VALIDATION_STATES = ("pending", "confirmed", "false_positive", "rejected")
SCHEMA_VERSION = "1.0"


def new_incident_id() -> str:
    return f"inc-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


@dataclass
class RuleAnalysis:
    suspected_root_cause: str | None
    confidence: float
    evidence_lines: list[str]
    burst_detected: bool
    burst_size: int
    error_lines: int
    noise_lines: int
    reasons: list[str] = field(default_factory=list)
    pattern_counts: dict[str, int] = field(default_factory=dict)
    numeric_signals: dict[str, float] = field(default_factory=dict)


@dataclass
class AIAnalysis:
    """Advisory LLM output. All fields are validated before storage."""

    available: bool
    model: str | None
    summary: str | None
    suspected_root_cause: str | None
    evidence_lines: list[str]
    confidence: float | None
    recommended_action: str | None
    status: str  # ok | insufficient_evidence | validation_error | unavailable | disabled | timeout
    error: str | None = None
    raw_excerpt: str | None = None
    agreed_with_rules: bool | None = None


@dataclass
class IncidentReport:
    incident_id: str
    timestamp: str
    service: str
    severity: str
    severity_decision: dict[str, Any]
    summary: str
    suspected_root_cause: str | None
    evidence_lines: list[str]
    confidence: float
    recommended_action: str
    analysis_source: str  # rule-based | llm-assisted
    status: str  # open
    validation: dict[str, Any]
    rule_analysis: dict[str, Any]
    ai_analysis: dict[str, Any]
    ground_truth: str | None = None
    window: dict[str, Any] = field(default_factory=dict)
    runbook_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    # -- serialization ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    # -- markdown --------------------------------------------------------
    def to_markdown(self) -> str:
        rule = self.rule_analysis or {}
        ai = self.ai_analysis or {}
        validation = self.validation or {}
        lines: list[str] = []
        lines.append(f"# Incident {self.incident_id}")
        lines.append("")
        lines.append(f"- **Timestamp:** {self.timestamp}")
        lines.append(f"- **Service:** {self.service}")
        lines.append(f"- **Severity:** {self.severity}")
        lines.append(f"- **Analysis source:** {self.analysis_source}")
        lines.append(f"- **Status:** {self.status}")
        lines.append(
            f"- **Suspected root cause:** {self.suspected_root_cause or 'unknown / insufficient evidence'}"
        )
        lines.append(f"- **Confidence:** {self.confidence:.2f}")
        if self.ground_truth:
            lines.append(f"- **Ground truth (evaluation):** {self.ground_truth}")
        lines.append(f"- **Human validation:** {validation.get('status', 'pending')}")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(self.summary or "_no summary_")
        lines.append("")
        lines.append("## Rule-based analysis")
        lines.append("")
        lines.append(f"- Suspected cause: `{rule.get('suspected_root_cause') or 'unknown'}`")
        lines.append(f"- Confidence: `{rule.get('confidence', 0):.2f}`")
        lines.append(f"- Error lines: `{rule.get('error_lines', 0)}`")
        lines.append(f"- Noise lines ignored: `{rule.get('noise_lines', 0)}`")
        burst = rule.get("burst_detected", False)
        lines.append(f"- Burst detected: `{burst}` (size {rule.get('burst_size', 0)})")
        if rule.get("reasons"):
            lines.append("- Reasoning:")
            for reason in rule["reasons"]:
                lines.append(f"  - {reason}")
        lines.append("")
        lines.append("## Evidence lines")
        lines.append("")
        if self.evidence_lines:
            lines.append("```")
            lines.extend(self.evidence_lines[:20])
            lines.append("```")
        else:
            lines.append("_no evidence lines captured_")
        lines.append("")
        lines.append("## AI-assisted analysis (advisory, unvalidated)")
        lines.append("")
        lines.append(f"- Status: `{ai.get('status')}`")
        lines.append(f"- Model: `{ai.get('model') or 'n/a'}`")
        if ai.get("available"):
            lines.append(f"- Suspected cause: `{ai.get('suspected_root_cause') or 'unknown'}`")
            conf = ai.get("confidence")
            lines.append(f"- Confidence: `{conf if conf is not None else 'n/a'}`")
            lines.append(f"- Agreed with rules: `{ai.get('agreed_with_rules')}`")
            if ai.get("summary"):
                lines.append(f"- Summary: {ai['summary']}")
            if ai.get("recommended_action"):
                lines.append(f"- Recommended action (advisory): {ai['recommended_action']}")
        else:
            lines.append(
                f"- _AI analysis unavailable ({ai.get('status')}); "
                "the rule-based report above stands on its own._"
            )
            if ai.get("error"):
                lines.append(f"- Detail: `{ai['error']}`")
        lines.append("")
        lines.append("## Recommended action (advisory - requires human validation)")
        lines.append("")
        lines.append(self.recommended_action or "_no action suggested_")
        lines.append("")
        if self.runbook_id:
            lines.append(
                f"Runbook: `{self.runbook_id}` (see docs/runbooks). "
                "No action is executed automatically."
            )
            lines.append("")
        lines.append("---")
        lines.append(
            "_This report was produced by deterministic analysis"
            + (" with advisory LLM assistance." if ai.get("available") else " without LLM assistance.")
            + " The LLM never executes commands; a human must validate the diagnosis._"
        )
        return "\n".join(lines) + "\n"


def build_report(
    *,
    service: str,
    severity: str,
    severity_decision: dict[str, Any],
    summary: str,
    rule_analysis: RuleAnalysis,
    ai_analysis: AIAnalysis,
    evidence_lines: list[str],
    recommended_action: str,
    window: dict[str, Any],
    ground_truth: str | None = None,
    runbook_id: str | None = None,
    incident_id: str | None = None,
) -> IncidentReport:
    """Assemble the final report, choosing the authoritative analysis source."""
    # The rule-based result is authoritative for the headline cause/severity.
    # If the LLM agrees and is available, we label the report 'llm-assisted';
    # otherwise it remains 'rule-based'. The LLM never overrides the rule verdict.
    analysis_source = "rule-based"
    if ai_analysis.available and ai_analysis.status == "ok":
        analysis_source = "llm-assisted"

    suspected = rule_analysis.suspected_root_cause
    confidence = rule_analysis.confidence

    report = IncidentReport(
        incident_id=incident_id or new_incident_id(),
        timestamp=utc_now_iso(),
        service=service,
        severity=severity,
        severity_decision=severity_decision,
        summary=summary,
        suspected_root_cause=suspected,
        evidence_lines=evidence_lines,
        confidence=confidence,
        recommended_action=recommended_action,
        analysis_source=analysis_source,
        status="open",
        validation={"status": "pending", "validated_by": None, "validated_at": None, "note": None},
        rule_analysis=asdict(rule_analysis),
        ai_analysis=asdict(ai_analysis),
        ground_truth=ground_truth,
        window=window,
        runbook_id=runbook_id,
    )
    return report


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "incident").strip()).strip("-").lower() or "incident"


class ReportStore:
    """Bounded local persistence for incident reports as JSON + Markdown.

    Reports are written to ``incidents/generated/<id>.json`` and ``.md``. The store
    keeps at most ``retention_limit`` incidents and prunes the oldest beyond that so
    storage never grows unbounded.
    """

    def __init__(self, directory: Path | str, retention_limit: int = 200) -> None:
        self.directory = Path(directory)
        self.retention_limit = max(1, retention_limit)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _paths(self, incident_id: str) -> tuple[Path, Path]:
        safe = slugify(incident_id)
        return self.directory / f"{safe}.json", self.directory / f"{safe}.md"

    def save(self, report: IncidentReport) -> tuple[Path, Path]:
        json_path, md_path = self._paths(report.incident_id)
        json_path.write_text(report.to_json(), encoding="utf-8")
        md_path.write_text(report.to_markdown(), encoding="utf-8")
        self._prune()
        return json_path, md_path

    def get(self, incident_id: str) -> dict[str, Any] | None:
        json_path, _ = self._paths(incident_id)
        if not json_path.exists():
            return None
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def get_markdown(self, incident_id: str) -> str | None:
        _, md_path = self._paths(incident_id)
        if not md_path.exists():
            return None
        try:
            return md_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def list(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for json_path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            summaries.append(
                {
                    "incident_id": data.get("incident_id"),
                    "timestamp": data.get("timestamp"),
                    "service": data.get("service"),
                    "severity": data.get("severity"),
                    "suspected_root_cause": data.get("suspected_root_cause"),
                    "analysis_source": data.get("analysis_source"),
                    "status": data.get("status"),
                    "validation": (data.get("validation") or {}).get("status"),
                    "ground_truth": data.get("ground_truth"),
                    "confidence": data.get("confidence"),
                }
            )
        summaries.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return summaries

    def set_validation(
        self, incident_id: str, status: str, validated_by: str | None = None, note: str | None = None
    ) -> dict[str, Any] | None:
        if status not in VALIDATION_STATES:
            raise ValueError(f"invalid validation status {status!r}; expected one of {VALIDATION_STATES}")
        data = self.get(incident_id)
        if data is None:
            return None
        data["validation"] = {
            "status": status,
            "validated_by": validated_by,
            "validated_at": utc_now_iso(),
            "note": note,
        }
        # Re-render both artifacts so the human decision is durable.
        json_path, md_path = self._paths(incident_id)
        json_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        report = _report_from_dict(data)
        if report is not None:
            md_path.write_text(report.to_markdown(), encoding="utf-8")
        return data

    def _prune(self) -> None:
        files = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
        excess = len(files) - self.retention_limit
        for json_path in files[: max(0, excess)]:
            md_path = json_path.with_suffix(".md")
            try:
                json_path.unlink(missing_ok=True)
                md_path.unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self) -> int:
        count = 0
        for path in list(self.directory.glob("*.json")) + list(self.directory.glob("*.md")):
            try:
                path.unlink()
                count += 1
            except OSError:
                pass
        return count


def _report_from_dict(data: dict[str, Any]) -> IncidentReport | None:
    """Best-effort reconstruction used only to re-render Markdown after validation."""
    try:
        return IncidentReport(
            incident_id=data["incident_id"],
            timestamp=data.get("timestamp", utc_now_iso()),
            service=data.get("service", "unknown"),
            severity=data.get("severity", "info"),
            severity_decision=data.get("severity_decision", {}),
            summary=data.get("summary", ""),
            suspected_root_cause=data.get("suspected_root_cause"),
            evidence_lines=data.get("evidence_lines", []),
            confidence=data.get("confidence", 0.0),
            recommended_action=data.get("recommended_action", ""),
            analysis_source=data.get("analysis_source", "rule-based"),
            status=data.get("status", "open"),
            validation=data.get("validation", {}),
            rule_analysis=data.get("rule_analysis", {}),
            ai_analysis=data.get("ai_analysis", {}),
            ground_truth=data.get("ground_truth"),
            window=data.get("window", {}),
            runbook_id=data.get("runbook_id"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )
    except (KeyError, TypeError, ValueError):
        return None
