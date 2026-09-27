"""Deterministic incident severity.

Severity is derived from objective signals only - error volume, burst behaviour and
the recognized incident type. The LLM never sets severity; it may only *describe*
impact in its advisory text. This keeps severity auditable and reproducible.

Documented in docs/incident-format.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analyzer.correlate import CorrelationResult

SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

# Known incident types get a sensible floor; volume/burst can raise them.
BASE_SEVERITY_BY_CAUSE = {
    "database_timeout": "high",
    "dependency_timeout": "high",
    "http_500_burst": "high",
    "memory_pressure": "high",
    "invalid_configuration": "critical",  # misconfiguration blocks correct operation
    "liveness_probe_failure": "critical",  # kubernetes will restart the pod
}


@dataclass(frozen=True)
class SeverityDecision:
    severity: str
    score: int
    factors: list[str]

    def to_dict(self) -> dict[str, object]:
        return {"severity": self.severity, "score": self.score, "factors": list(self.factors)}


def _rank(severity: str) -> int:
    return SEVERITY_RANK.get(severity, 0)


def _name_for_rank(rank: int) -> str:
    for name, value in SEVERITY_RANK.items():
        if value == rank:
            return name
    return "info"


def compute_severity(result: CorrelationResult) -> SeverityDecision:
    """Compute incident severity from deterministic signals.

    Scoring:
      * recognized incident type contributes its base rank
      * +1 when a correlated burst is detected
      * +1 when error volume is high (>= 20 error lines in the window)
      * a CRITICAL log line in the evidence raises severity to at least 'high'
      * no suspected cause -> 'info' (nothing actionable detected)
    """
    factors: list[str] = []

    if result.suspected_root_cause is None:
        if result.error_lines == 0:
            factors.append("no error lines in window; no incident detected")
            return SeverityDecision("info", 0, factors)
        # Errors present but uncorrelated / below threshold.
        rank = min(2, _rank("medium") if result.error_lines >= 3 else _rank("low"))
        factors.append(
            f"{result.error_lines} uncorrelated error line(s); insufficient evidence for a specific cause"
        )
        return SeverityDecision(_name_for_rank(rank), rank, factors)

    cause = result.suspected_root_cause
    rank = _rank(BASE_SEVERITY_BY_CAUSE.get(cause, "medium"))
    factors.append(f"recognized incident type '{cause}' -> base '{_name_for_rank(rank)}'")

    if result.burst_detected:
        rank = min(4, rank + 1)
        factors.append(f"correlated burst of {result.burst_size} errors -> +1")

    if result.error_lines >= 20:
        rank = min(4, rank + 1)
        factors.append(f"high error volume ({result.error_lines} lines) -> +1")

    has_critical = any((r.severity or "").upper() == "CRITICAL" for r in result.evidence_records)
    if has_critical and rank < _rank("high"):
        rank = _rank("high")
        factors.append("CRITICAL log line present -> floor 'high'")

    return SeverityDecision(_name_for_rank(rank), rank, factors)


def severity_meets_threshold(severity: str, minimum: str = "low") -> bool:
    """Whether ``severity`` is at least ``minimum`` (used by notification gating)."""
    return _rank(severity) >= _rank(minimum)
