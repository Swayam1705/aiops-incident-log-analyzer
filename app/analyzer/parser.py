"""Log parser: raw line -> structured :class:`ParsedRecord`.

Design goals, in order:

1. **Never crash on bad input.** A malformed line becomes a ``parsed=False`` record
   with ``classification="noise"``; it never raises and never aborts a batch.
2. **Extract what the analyzer needs**: timestamp, service, severity, request_id,
   message/error, and a normalized ``error_pattern``.
3. **Stay deterministic.** Pattern matching is pure regex/string work with no
   randomness and no I/O.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

SEVERITY_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
    "ERR": 40,
    "CRITICAL": 50,
    "FATAL": 50,
    "PANIC": 50,
}

ERROR_SEVERITIES = {"ERROR", "ERR", "CRITICAL", "FATAL", "PANIC"}
WARNING_SEVERITIES = {"WARNING", "WARN"}

# ISO-8601 with optional fractional seconds and Z/offset suffix.
_TS_PATTERN = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)

# Normalized error patterns recognized by the deterministic analyzer. Each maps to
# the same vocabulary used as ground-truth labels so rule-based detection and
# evaluation line up. The ``error.type`` field, when present, is authoritative;
# these regexes are the fallback for logs that only carry a message.
PATTERN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "database_timeout",
        re.compile(
            r"(database|postgres|postgresql|mysql|\bdb\b).{0,40}(timed out|timeout|statement timeout)"
            r"|(statement timeout)|(connection pool exhausted)",
            re.IGNORECASE,
        ),
    ),
    (
        "memory_pressure",
        re.compile(
            r"(memory|heap|\brss\b|oom|out of memory|bad_alloc).{0,40}"
            r"(pressure|high watermark|allocation failed|limit|growing|exhausted|kill)"
            r"|(memory allocation failed)|(oomkill)|(high watermark)",
            re.IGNORECASE,
        ),
    ),
    (
        "dependency_timeout",
        re.compile(
            r"(dependency|upstream|downstream|payment-gateway|circuit breaker).{0,40}"
            r"(timed out|timeout|retry|retries|opened|unavailable)"
            r"|(circuit breaker opened)|(read timed out)",
            re.IGNORECASE,
        ),
    ),
    (
        "http_500_burst",
        re.compile(
            r"(unhandled exception|internal server error|http 500|status.?500|500 internal)"
            r"|(returning http 500)",
            re.IGNORECASE,
        ),
    ),
    (
        "invalid_configuration",
        re.compile(
            r"(invalid configuration|configuration (is )?missing|config(uration)? (key )?"
            r"(missing|invalid|rejected)|schema validation failed|required configuration)",
            re.IGNORECASE,
        ),
    ),
    (
        "liveness_probe_failure",
        re.compile(r"liveness (check|probe) failed|self-test did not complete", re.IGNORECASE),
    ),
)

_TYPE_LABELS = {
    "database_timeout": "database_timeout",
    "memory_pressure": "memory_pressure",
    "http_500_burst": "http_500_burst",
    "dependency_timeout": "dependency_timeout",
    "invalid_configuration": "invalid_configuration",
    "liveness_probe_failure": "liveness_probe_failure",
}


@dataclass
class ParsedRecord:
    """A single normalized log record.

    ``parsed`` is False for lines that could not be interpreted; such records are
    retained (as evidence of noise) but excluded from correlation.
    """

    raw: str
    parsed: bool
    timestamp: str | None = None
    service: str | None = None
    severity: str | None = None
    request_id: str | None = None
    message: str | None = None
    event: str | None = None
    error_pattern: str | None = None
    error_type: str | None = None
    status: int | None = None
    dependency: str | None = None
    config_key: str | None = None
    numeric: dict[str, float] = field(default_factory=dict)
    classification: str = "info"  # error | warning | info | noise
    source: str = "unknown"
    line_no: int = 0

    def severity_rank(self) -> int:
        return SEVERITY_ORDER.get((self.severity or "").upper(), 0)

    def is_error(self) -> bool:
        return (self.severity or "").upper() in ERROR_SEVERITIES

    def is_warning(self) -> bool:
        return (self.severity or "").upper() in WARNING_SEVERITIES

    def to_evidence(self, max_chars: int = 400) -> str:
        """Compact single-line rendering used as report/LLM evidence."""
        head = f"[{self.timestamp or '-'}] {self.severity or '-'} {self.service or '-'}"
        body = self.message or self.raw.strip()
        if len(body) > max_chars:
            body = body[: max_chars - 3] + "..."
        pattern = f" pattern={self.error_pattern}" if self.error_pattern else ""
        return f"{head} {body}{pattern}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "service": self.service,
            "severity": self.severity,
            "request_id": self.request_id,
            "message": self.message,
            "event": self.event,
            "error_pattern": self.error_pattern,
            "error_type": self.error_type,
            "status": self.status,
            "classification": self.classification,
        }


def _normalize_severity(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if not text:
        return None
    if text in SEVERITY_ORDER:
        return "WARNING" if text == "WARN" else ("ERROR" if text == "ERR" else text)
    # tolerate numeric syslog-ish levels
    try:
        numeric = int(float(text))
    except ValueError:
        return None
    for name, rank in (("CRITICAL", 50), ("ERROR", 40), ("WARNING", 30), ("INFO", 20), ("DEBUG", 10)):
        if numeric >= rank:
            return name
    return None


def _detect_pattern(text: str) -> str | None:
    for label, pattern in PATTERN_RULES:
        if pattern.search(text):
            return label
    return None


def _coerce_numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _extract_numeric_fields(payload: dict[str, Any]) -> dict[str, float]:
    interesting = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if any(
            token in lowered
            for token in (
                "rss", "bytes", "percent", "elapsed", "timeout",
                "duration", "pool", "waiters", "count", "limit", "usage",
            )
        ):
            numeric = _coerce_numeric(value)
            if numeric is not None:
                interesting[key] = numeric
    return interesting


def parse_line(line: str, source: str = "unknown", line_no: int = 0) -> ParsedRecord:
    """Parse one raw log line into a :class:`ParsedRecord`. Never raises."""
    stripped = (line or "").strip()
    if not stripped:
        return ParsedRecord(
            raw=line or "", parsed=False, classification="noise", source=source, line_no=line_no
        )

    payload: dict[str, Any] | None = None
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            loaded = json.loads(stripped)
            if isinstance(loaded, dict):
                payload = loaded
        except (json.JSONDecodeError, ValueError):
            payload = None

    if payload is None:
        # Non-JSON line: treat as noise unless it clearly carries a timestamp+level.
        match = _TS_PATTERN.match(stripped)
        record = ParsedRecord(
            raw=line,
            parsed=False,
            classification="noise",
            source=source,
            line_no=line_no,
            timestamp=match.group("ts") if match else None,
            message=stripped,
        )
        # A legacy text line may still be an error; detect severity/pattern best-effort.
        upper = stripped.upper()
        for token in ("CRITICAL", "FATAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG"):
            if f" {token} " in f" {upper} ":
                record.severity = _normalize_severity(token)
                record.parsed = True
                record.classification = (
                    "error"
                    if token in ("CRITICAL", "FATAL", "ERROR")
                    else "warning"
                    if token in ("WARNING", "WARN")
                    else "info"
                )
                break
        record.error_pattern = _detect_pattern(stripped)
        return record

    # --- structured JSON record ---
    timestamp = payload.get("timestamp") or payload.get("time") or payload.get("@timestamp")
    severity = _normalize_severity(payload.get("level") or payload.get("severity") or payload.get("lvl"))
    service = payload.get("service") or payload.get("app") or payload.get("logger")
    request_id = payload.get("request_id") or payload.get("requestId") or payload.get("trace_id")
    message = payload.get("message") or payload.get("msg") or payload.get("event")
    event = payload.get("event")

    error = payload.get("error")
    error_type_raw = None
    error_detail_parts: list[str] = []
    if isinstance(error, dict):
        error_type_raw = error.get("type") or error.get("code")
        for key in ("detail", "code", "exception"):
            if error.get(key):
                error_detail_parts.append(str(error[key]))
    elif isinstance(error, str):
        error_detail_parts.append(error)

    haystack = " ".join(
        str(part)
        for part in [message, event, *error_detail_parts, error_type_raw]
        if part is not None
    )
    normalized_type = _TYPE_LABELS.get(str(error_type_raw).lower()) if error_type_raw else None
    error_pattern = normalized_type or _detect_pattern(haystack)

    classification = "info"
    if severity in ERROR_SEVERITIES:
        classification = "error"
    elif severity in WARNING_SEVERITIES:
        classification = "warning"

    status = _coerce_numeric(payload.get("status"))
    record = ParsedRecord(
        raw=line,
        parsed=True,
        timestamp=str(timestamp) if timestamp is not None else None,
        service=str(service) if service is not None else None,
        severity=severity,
        request_id=str(request_id) if request_id is not None else None,
        message=str(message) if message is not None else None,
        event=str(event) if event is not None else None,
        error_pattern=error_pattern,
        error_type=str(error_type_raw) if error_type_raw is not None else None,
        status=int(status) if status is not None else None,
        dependency=payload.get("dependency"),
        config_key=payload.get("config_key"),
        numeric=_extract_numeric_fields(payload),
        classification=classification,
        source=source,
        line_no=line_no,
    )
    # A record can carry an error pattern even at WARNING (e.g. retries, pool waits);
    # keep the classification severity-accurate but surface the pattern.
    return record


def parse_lines(lines: list[str], source: str = "unknown") -> list[ParsedRecord]:
    """Parse a batch of lines; always returns one record per input line."""
    return [parse_line(line, source=source, line_no=index) for index, line in enumerate(lines)]
