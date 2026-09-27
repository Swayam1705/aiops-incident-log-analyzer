"""Structured JSON logging with a rotating file sink and secret redaction.

Every record emitted by the application is a single JSON object on one line, e.g.::

    {"timestamp":"2026-09-26T10:00:00.000Z","level":"ERROR","service":"checkout-service",
     "logger":"app.request","request_id":"req-1","event":"db_query_failed",
     "message":"database query timed out","error":{"type":"database_timeout"},...}

Two guarantees matter for this project:

* **The analyzer can always parse the file.** Records are JSON-first; anything that
  is not JSON is still written (as a text record) and the parser handles it as noise.
* **Secrets never reach the log file.** :class:`RedactingFilter` masks values of
  sensitive keys and known secret-looking tokens before formatting.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "cookie",
    "access_key",
    "private_key",
    "connection_string",
    "dsn",
}

SENSITIVE_SUBSTRINGS = ("password", "secret", "token", "api_key", "apikey", "authorization")

_SECRET_LOOKALIKE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|authorization)\b\s*[:=]\s*(\S+)"
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]+")
_REDACTED = "***REDACTED***"

RESERVED_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


def utc_now_iso() -> str:
    """Millisecond-precision UTC timestamp, the format used in every log record."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _redact_text(text: str) -> str:
    if not text:
        return text
    text = _SECRET_LOOKALIKE.sub(lambda m: m.group(0).replace(m.group(1), _REDACTED), text)
    return _BEARER.sub(lambda m: m.group(1) + _REDACTED, text)


def redact_value(key: str, value: Any) -> Any:
    """Mask a value when its key looks sensitive; recurse into mappings."""
    lowered = str(key).lower()
    if lowered in SENSITIVE_KEYS or any(part in lowered for part in SENSITIVE_SUBSTRINGS):
        return _REDACTED
    if isinstance(value, dict):
        return {k: redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(key, item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secret-looking data from records."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API name
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact_value(k, v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _redact_text(a) if isinstance(a, str) else a for a in record.args
                )
        return True


class StructuredFormatter(logging.Formatter):
    """Emit one JSON object per record."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:  # noqa: A002 - logging API name
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in RESERVED_ATTRS}
        for key, value in extra.items():
            payload[key] = redact_value(key, value)
        message = record.getMessage()
        payload["message"] = _redact_text(message)
        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    """Human-readable fallback used when LOG_FORMAT=text (development only)."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:  # noqa: A002 - logging API name
        base = (
            f"{datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec='seconds')}"
            f" {record.levelname:<5} {self.service} {record.name} :: {record.getMessage()}"
        )
        extra = {k: v for k, v in record.__dict__.items() if k not in RESERVED_ATTRS}
        if extra:
            base += " " + json.dumps(
                {k: redact_value(k, v) for k, v in extra.items()}, default=str, separators=(",", ":")
            )
        return _redact_text(base)


class RotatingFileSink:
    """Size-rotating JSON file sink with an explicit writer used by the simulator.

    The application logs through the standard ``logging`` module; the in-process log
    generator writes through :meth:`write_line` so that a generated record can be
    handed to the analyzer in tests without touching disk.
    """

    def __init__(self, path: Path, max_bytes: int) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = logging.handlers.RotatingFileHandler(
            self.path, maxBytes=max_bytes, backupCount=3, encoding="utf-8"
        )

    def write_line(self, payload: dict[str, Any]) -> str:
        """Append a pre-built structured record; returns the serialized line."""
        line = json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                self._rotate()
        except OSError:
            # Disk full / read-only volume: never crash the request path.
            pass
        return line

    def _rotate(self) -> None:
        for index in range(2, 0, -1):
            src = self.path.with_suffix(self.path.suffix + f".{index}")
            if src.exists():
                src.replace(self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        backup = self.path.with_suffix(self.path.suffix + ".1")
        try:
            self.path.replace(backup)
            self.path.touch()
        except OSError:
            pass


def configure_logging(
    service: str,
    level: str = "INFO",
    log_file: Path | None = None,
    log_format: str = "json",
    max_bytes: int = 10 * 1024 * 1024,
) -> RotatingFileSink | None:
    """Configure root logging: structured stdout + rotating structured file."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter: logging.Formatter = (
        StructuredFormatter(service) if log_format != "text" else TextFormatter(service)
    )
    redactor = RedactingFilter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(redactor)
    root.addHandler(stream)

    sink: RotatingFileSink | None = None
    if log_file is not None:
        try:
            sink = RotatingFileSink(Path(log_file), max_bytes)
            file_handler = logging.StreamHandler(sink._handler.stream)  # noqa: SLF001
            file_handler.setFormatter(formatter)
            file_handler.addFilter(redactor)
            root.addHandler(file_handler)
        except OSError:
            root.warning("log file sink unavailable at %s; continuing with stdout only", log_file)
            sink = None

    # Third-party noise reduction keeps generated logs analyzable.
    for noisy in ("uvicorn.access", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return sink


def write_structured(
    sink: RotatingFileSink | None,
    logger: logging.Logger,
    level: str,
    event: str,
    message: str,
    **fields: Any,
) -> dict[str, Any]:
    """Build a structured record, log it, and persist it through the file sink.

    Returns the record so callers (and tests) can assert on the exact payload.
    """
    record: dict[str, Any] = {
        "timestamp": utc_now_iso(),
        "level": level.upper(),
        "service": fields.pop("service", None) or "checkout-service",
        "logger": logger.name,
        "event": event,
        "message": message,
    }
    for key, value in fields.items():
        record[key] = redact_value(key, value)
    if sink is not None:
        sink.write_line(record)
    log_fn = getattr(logger, level.lower(), logger.info)
    log_fn(message, extra={k: v for k, v in record.items() if k not in ("timestamp", "level", "message")})
    return record


def monotonic_ms() -> float:
    return time.monotonic() * 1000.0


def log_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
