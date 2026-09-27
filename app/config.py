"""Typed configuration for the application and the analyzer.

Every knob is an environment variable so the same code runs unchanged on a laptop
(local/Docker mode) and inside Kubernetes (ConfigMap-driven mode).

Nothing here reads secrets. The only credential-shaped variable in the whole
project is the optional notification webhook token, which is read at call time
and is never logged or serialised into a report.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, str] = {
    # --- service ---
    "APP_NAME": "checkout-service",
    "APP_HOST": "0.0.0.0",
    "APP_PORT": "8000",
    "APP_ENV": "local",
    # --- logging / log collection ---
    "LOG_LEVEL": "INFO",
    "LOG_FILE": "logs/app.log",
    "LOG_FORMAT": "json",  # json | text
    "LOG_MAX_BYTES": "10485760",  # 10 MiB per log file
    # --- log windowing (bounded analysis) ---
    "LOG_WINDOW_SIZE": "2000",
    "COLLECTOR_MAX_BYTES": "4194304",  # 4 MiB tail read per file
    "COLLECTOR_FILE_GLOB": "logs/*.log",
    # --- incident triggers / thresholds ---
    "ANALYZE_MIN_ERROR_LINES": "5",
    "ANALYZE_BURST_WINDOW_SECONDS": "60",
    "ANALYZE_BURST_GAP_SECONDS": "15",
    "ANALYZE_INTERVAL_SECONDS": "0",  # 0 = scheduled analysis disabled (manual/triggered only)
    "ANALYZE_ENABLED": "true",
    # --- Ollama (local model) ---
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "qwen2.5:1.5b",
    "LLM_ENABLED": "true",
    "LLM_TIMEOUT_SECONDS": "45",
    "LLM_MAX_CONTEXT_LINES": "60",
    "LLM_MAX_RESPONSE_CHARS": "8000",
    "LLM_TEMPERATURE": "0.0",
    # --- notification ---
    "NOTIFICATION_WEBHOOK_URL": "",
    "NOTIFICATION_TIMEOUT_SECONDS": "5",
    # --- persistence ---
    "INCIDENTS_DIR": "incidents/generated",
    "REPORT_RETENTION_LIMIT": "200",
    # --- failure injection (simulations only) ---
    "FAULT_DURATION_SECONDS": "20",
    "SIMULATOR_ENABLED": "true",
    "SIMULATOR_INTERVAL_SECONDS": "1",
    "SIMULATOR_LOGS_PER_TICK": "2",
    "MEMORY_PRESSURE_SIMULATE_MB": "0",  # 0 = log-only simulation (no real allocation)
    "MEMORY_PRESSURE_MAX_MB": "256",
    "ADMIN_TOKEN": "",
}

_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off", ""}


def _raw_env() -> dict[str, str]:
    """Return the environment, optionally overlaid with AIOps_CONFIG_JSON (used by tests)."""
    env = dict(os.environ)
    overlay = env.get("AIOPS_CONFIG_JSON")
    if overlay:
        try:
            data = json.loads(overlay)
        except json.JSONDecodeError:
            return env
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(key, str) and key.isupper():
                    env[key] = "" if value is None else str(value)
    return env


@dataclass
class Settings:
    """Runtime settings resolved once at start-up."""

    app_name: str = DEFAULTS["APP_NAME"]
    host: str = DEFAULTS["APP_HOST"]
    port: int = 8000
    env: str = "local"

    log_level: str = "INFO"
    log_file: Path = field(default_factory=lambda: Path("logs/app.log"))
    log_format: str = "json"
    log_max_bytes: int = 10 * 1024 * 1024

    log_window_size: int = 2000
    collector_max_bytes: int = 4 * 1024 * 1024
    collector_file_glob: str = "logs/*.log"

    analyze_min_error_lines: int = 5
    analyze_burst_window_seconds: int = 60
    analyze_burst_gap_seconds: int = 15
    analyze_interval_seconds: int = 0
    analyze_enabled: bool = True

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"
    llm_enabled: bool = True
    llm_timeout_seconds: float = 45.0
    llm_max_context_lines: int = 60
    llm_max_response_chars: int = 8000
    llm_temperature: float = 0.0

    notification_webhook_url: str = ""
    notification_timeout_seconds: float = 5.0

    incidents_dir: Path = field(default_factory=lambda: Path("incidents/generated"))
    report_retention_limit: int = 200

    fault_duration_seconds: int = 20
    simulator_enabled: bool = True
    simulator_interval_seconds: float = 1.0
    simulator_logs_per_tick: int = 2
    memory_pressure_simulate_mb: int = 0
    memory_pressure_max_mb: int = 256
    admin_token: str = ""

    base_dir: Path = field(default_factory=lambda: Path.cwd())

    # -- derived helpers -------------------------------------------------
    @property
    def notification_enabled(self) -> bool:
        return bool(self.notification_webhook_url)

    @property
    def llm_requested(self) -> bool:
        """True when the operator asked for LLM assistance and configured a model."""
        return self.llm_enabled and bool(self.ollama_model.strip())

    def resolve(self, path: Path | str) -> Path:
        """Resolve a possibly-relative path against the project base directory."""
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return (self.base_dir / candidate).resolve()

    def log_files(self) -> list[Path]:
        """Log files to collect from: the configured log file plus glob matches.

        Always includes ``LOG_FILE`` (the file this process writes to) so the
        analyzer reads what the application actually emits, even if the glob and the
        log path were configured differently. Results are de-duplicated and sorted.
        """
        found: set[Path] = set()
        primary = self.resolve(self.log_file)
        if primary.exists():
            found.add(primary)
        pattern = self.resolve(self.collector_file_glob)
        if "*" in str(pattern):
            base = pattern.parent if pattern.parent != Path() else self.base_dir
            try:
                base.mkdir(parents=True, exist_ok=True)
                found.update(p for p in base.glob(pattern.name) if p.is_file())
            except OSError:
                pass
        elif pattern.exists():
            found.add(pattern)
        return sorted(found)

    def to_public_dict(self) -> dict[str, Any]:
        """Configuration safe to expose on /health. Contains no secrets."""
        return {
            "app_name": self.app_name,
            "env": self.env,
            "log_format": self.log_format,
            "log_window_size": self.log_window_size,
            "analyze_min_error_lines": self.analyze_min_error_lines,
            "analyze_enabled": self.analyze_enabled,
            "analyze_interval_seconds": self.analyze_interval_seconds,
            "llm_enabled": self.llm_enabled,
            "ollama_base_url": self.ollama_base_url,
            "ollama_model": self.ollama_model,
            "llm_max_context_lines": self.llm_max_context_lines,
            "notification_enabled": self.notification_enabled,
            "incidents_dir": str(self.resolve(self.incidents_dir)),
            "report_retention_limit": self.report_retention_limit,
            "simulator_enabled": self.simulator_enabled,
            "admin_auth_enabled": bool(self.admin_token),
        }


def _as_int(value: str, fallback: int, minimum: int | None = None) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    if minimum is not None and parsed < minimum:
        return fallback
    return parsed


def _as_float(value: str, fallback: float, minimum: float | None = None) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    if minimum is not None and parsed < minimum:
        return fallback
    return parsed


def _as_bool(value: str, fallback: bool) -> bool:
    lowered = str(value).strip().lower()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    return fallback


def load_settings(env: dict[str, str] | None = None, base_dir: Path | str | None = None) -> Settings:
    """Build :class:`Settings` from an environment mapping (defaults to the process env).

    Invalid values fall back to documented defaults instead of crashing start-up.
    """
    source = dict(DEFAULTS)
    source.update(_raw_env() if env is None else env)

    root = Path(base_dir).resolve() if base_dir is not None else Path.cwd()

    settings = Settings(
        app_name=source.get("APP_NAME") or DEFAULTS["APP_NAME"],
        host=source.get("APP_HOST") or "0.0.0.0",
        port=_as_int(source.get("APP_PORT", "8000"), 8000, minimum=1),
        env=source.get("APP_ENV") or "local",
        log_level=(source.get("LOG_LEVEL") or "INFO").upper(),
        log_file=Path(source.get("LOG_FILE") or DEFAULTS["LOG_FILE"]),
        log_format=(source.get("LOG_FORMAT") or "json").lower(),
        log_max_bytes=_as_int(source.get("LOG_MAX_BYTES", ""), 10 * 1024 * 1024, minimum=1024),
        log_window_size=_as_int(source.get("LOG_WINDOW_SIZE", "2000"), 2000, minimum=1),
        collector_max_bytes=_as_int(
            source.get("COLLECTOR_MAX_BYTES", ""), 4 * 1024 * 1024, minimum=1024
        ),
        collector_file_glob=source.get("COLLECTOR_FILE_GLOB") or DEFAULTS["COLLECTOR_FILE_GLOB"],
        analyze_min_error_lines=_as_int(source.get("ANALYZE_MIN_ERROR_LINES", "5"), 5, minimum=1),
        analyze_burst_window_seconds=_as_int(
            source.get("ANALYZE_BURST_WINDOW_SECONDS", "60"), 60, minimum=1
        ),
        analyze_burst_gap_seconds=_as_int(source.get("ANALYZE_BURST_GAP_SECONDS", "15"), 15, minimum=1),
        analyze_interval_seconds=_as_int(source.get("ANALYZE_INTERVAL_SECONDS", "0"), 0, minimum=0),
        analyze_enabled=_as_bool(source.get("ANALYZE_ENABLED", "true"), True),
        ollama_base_url=(source.get("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/"),
        # Respect an explicitly empty OLLAMA_MODEL (means "no model"); only fall back
        # to the default when the variable is absent (handled by DEFAULTS merge).
        ollama_model=str(source.get("OLLAMA_MODEL", "")).strip(),
        llm_enabled=_as_bool(source.get("LLM_ENABLED", "true"), True),
        llm_timeout_seconds=_as_float(source.get("LLM_TIMEOUT_SECONDS", "45"), 45.0, minimum=1.0),
        llm_max_context_lines=_as_int(source.get("LLM_MAX_CONTEXT_LINES", "60"), 60, minimum=1),
        llm_max_response_chars=_as_int(
            source.get("LLM_MAX_RESPONSE_CHARS", "8000"), 8000, minimum=200
        ),
        llm_temperature=_as_float(source.get("LLM_TEMPERATURE", "0.0"), 0.0, minimum=0.0),
        notification_webhook_url=(source.get("NOTIFICATION_WEBHOOK_URL") or "").strip(),
        notification_timeout_seconds=_as_float(
            source.get("NOTIFICATION_TIMEOUT_SECONDS", "5"), 5.0, minimum=0.5
        ),
        incidents_dir=Path(source.get("INCIDENTS_DIR") or DEFAULTS["INCIDENTS_DIR"]),
        report_retention_limit=_as_int(source.get("REPORT_RETENTION_LIMIT", "200"), 200, minimum=1),
        fault_duration_seconds=_as_int(source.get("FAULT_DURATION_SECONDS", "20"), 20, minimum=1),
        simulator_enabled=_as_bool(source.get("SIMULATOR_ENABLED", "true"), True),
        simulator_interval_seconds=_as_float(
            source.get("SIMULATOR_INTERVAL_SECONDS", "1"), 1.0, minimum=0.05
        ),
        simulator_logs_per_tick=_as_int(source.get("SIMULATOR_LOGS_PER_TICK", "2"), 2, minimum=0),
        memory_pressure_simulate_mb=_as_int(
            source.get("MEMORY_PRESSURE_SIMULATE_MB", "0"), 0, minimum=0
        ),
        memory_pressure_max_mb=_as_int(source.get("MEMORY_PRESSURE_MAX_MB", "256"), 256, minimum=1),
        admin_token=source.get("ADMIN_TOKEN") or "",
        base_dir=root,
    )
    return settings
