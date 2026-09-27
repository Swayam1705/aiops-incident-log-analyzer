"""Deterministic, rule-based correlation and root-cause inference.

This is the *non-AI* analysis stage required by the project. It runs first, always,
and produces a useful incident hypothesis even when Ollama is unavailable. The LLM
stage later consumes this result as *evidence*, never as a replacement.

Everything here is pure and deterministic: same parsed records in, same result out.
No randomness, no I/O, no model calls.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.analyzer.parser import ParsedRecord

# Confidence assigned to a rule-based hypothesis when a strong typed signal is present.
TYPED_CONFIDENCE = 0.9
PATTERN_ONLY_CONFIDENCE = 0.72
WEAK_CONFIDENCE = 0.5


@dataclass
class CorrelationResult:
    """Output of the deterministic correlation stage."""

    total_lines: int
    parsed_lines: int
    noise_lines: int
    error_lines: int
    warning_lines: int
    window_start: str | None
    window_end: str | None
    services: dict[str, int]
    pattern_counts: dict[str, int]
    top_pattern: str | None
    suspected_root_cause: str | None
    rule_confidence: float
    burst_detected: bool
    burst_size: int
    evidence_records: list[ParsedRecord]
    per_pattern_services: dict[str, dict[str, int]] = field(default_factory=dict)
    numeric_signals: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def has_incident(self) -> bool:
        return self.suspected_root_cause is not None

    def evidence_lines(self, limit: int = 12, max_chars: int = 400) -> list[str]:
        return [record.to_evidence(max_chars=max_chars) for record in self.evidence_records[:limit]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_lines": self.total_lines,
            "parsed_lines": self.parsed_lines,
            "noise_lines": self.noise_lines,
            "error_lines": self.error_lines,
            "warning_lines": self.warning_lines,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "services": self.services,
            "pattern_counts": self.pattern_counts,
            "top_pattern": self.top_pattern,
            "suspected_root_cause": self.suspected_root_cause,
            "rule_confidence": self.rule_confidence,
            "burst_detected": self.burst_detected,
            "burst_size": self.burst_size,
            "numeric_signals": self.numeric_signals,
            "reasons": self.reasons,
        }


def _burst_size(error_records: list[ParsedRecord]) -> tuple[int, bool]:
    """Size of the largest cluster of errors sharing a dominant error_pattern.

    A "burst" is a repeated run of the same error pattern; this is what separates a
    real incident from sporadic unrelated errors.
    """
    if not error_records:
        return 0, False
    grouped: dict[str, list[ParsedRecord]] = defaultdict(list)
    for record in error_records:
        key = record.error_pattern or "_unclassified"
        grouped[key].append(record)
    largest_key = max(grouped, key=lambda key: len(grouped[key]))
    size = len(grouped[largest_key])
    return size, size >= 2


def correlate(
    records: list[ParsedRecord],
    min_error_lines: int = 5,
) -> CorrelationResult:
    """Correlate parsed records into a deterministic root-cause hypothesis."""
    total = len(records)
    parsed = [r for r in records if r.parsed]
    noise = [r for r in records if not r.parsed]
    errors = [r for r in parsed if r.is_error()]
    warnings = [r for r in parsed if r.is_warning()]

    timestamps = [r.timestamp for r in parsed if r.timestamp]
    window_start = min(timestamps) if timestamps else None
    window_end = max(timestamps) if timestamps else None

    service_counts: Counter[str] = Counter()
    for record in parsed:
        if record.service:
            service_counts[record.service] += 1

    # Count error patterns across ERROR and CRITICAL lines primarily, but also
    # consider WARNING lines that carry a typed pattern (retries, pool waits) so a
    # dependency/database incident isn't missed when errors are interleaved.
    pattern_counts: Counter[str] = Counter()
    per_pattern_services: dict[str, Counter[str]] = defaultdict(Counter)
    for record in errors:
        if record.error_pattern:
            pattern_counts[record.error_pattern] += 1
            if record.service:
                per_pattern_services[record.error_pattern][record.service] += 1

    typed_errors = [r for r in errors if r.error_type]
    typed_pattern_counts: Counter[str] = Counter(
        r.error_pattern for r in typed_errors if r.error_pattern
    )

    top_pattern: str | None = None
    suspected: str | None = None
    confidence = 0.0
    reasons: list[str] = []

    burst_size, burst_detected = _burst_size(errors)

    # Prefer the strongest *typed* signal; fall back to message-pattern matching.
    if typed_pattern_counts:
        top_pattern = typed_pattern_counts.most_common(1)[0][0]
    elif pattern_counts:
        top_pattern = pattern_counts.most_common(1)[0][0]

    error_total = len(errors)
    if top_pattern:
        pattern_hits = pattern_counts.get(top_pattern, 0)
        share = (pattern_hits / error_total) if error_total else 0.0
        if typed_pattern_counts.get(top_pattern, 0) > 0:
            confidence = TYPED_CONFIDENCE
            reasons.append(
                f"{pattern_hits} error(s) carry typed error.type={top_pattern}"
            )
        else:
            confidence = PATTERN_ONLY_CONFIDENCE
            reasons.append(
                f"{pattern_hits} error(s) matched message pattern '{top_pattern}'"
            )
        if share >= 0.5:
            confidence = min(0.97, confidence + 0.05)
            reasons.append(f"pattern '{top_pattern}' dominates {share:.0%} of errors")
        if burst_detected:
            reasons.append(f"detected burst of {burst_size} correlated errors")
        # Only surface a hypothesis when there is enough correlated evidence.
        if error_total >= min_error_lines and (typed_pattern_counts.get(top_pattern, 0) or share >= 0.4):
            suspected = top_pattern
        else:
            reasons.append(
                f"evidence below threshold (errors={error_total}, min={min_error_lines}); "
                "reporting unknown/insufficient-evidence"
            )
            suspected = None
    else:
        reasons.append("no recognized error pattern in window; nothing to correlate")

    if suspected is None and error_total and error_total < min_error_lines:
        reasons.append(
            f"only {error_total} error line(s), below min_error_lines={min_error_lines}"
        )

    # Evidence = the correlated error lines for the suspected pattern (or, when
    # unknown, the most severe error lines) so a human reviewer sees real data.
    if suspected:
        evidence = [r for r in errors if r.error_pattern == suspected]
    else:
        evidence = sorted(errors, key=lambda r: -r.severity_rank())

    # Aggregate numeric signals (max RSS, max elapsed, etc.) to enrich the report.
    numeric_signals: dict[str, float] = {}
    for record in errors:
        for key, value in record.numeric.items():
            if key not in numeric_signals or value > numeric_signals[key]:
                numeric_signals[key] = value

    return CorrelationResult(
        total_lines=total,
        parsed_lines=len(parsed),
        noise_lines=len(noise),
        error_lines=error_total,
        warning_lines=len(warnings),
        window_start=window_start,
        window_end=window_end,
        services=dict(service_counts),
        pattern_counts=dict(pattern_counts),
        top_pattern=top_pattern,
        suspected_root_cause=suspected,
        rule_confidence=round(confidence, 3) if suspected else 0.0,
        burst_detected=burst_detected,
        burst_size=burst_size,
        evidence_records=evidence[:50],
        per_pattern_services={k: dict(v) for k, v in per_pattern_services.items()},
        numeric_signals=numeric_signals,
        reasons=reasons,
    )


def is_noisy_but_harmless(result: CorrelationResult) -> bool:
    """True when the window is dominated by noise/info with no incident signal.

    Used to demonstrate that irrelevant log lines do not create false incidents.
    """
    return (
        result.suspected_root_cause is None
        and result.error_lines == 0
        and (result.noise_lines > 0 or result.parsed_lines > 0)
    )
