"""Deterministic structured log generation for the demo workload.

This module produces the *log records* that the application emits - both for normal
traffic and for each simulated failure scenario. It is the single source of truth
for log shape, which keeps three consumers consistent:

* the running service (writes records to stdout + the rotating JSON log file)
* the offline evaluation harness (generates fixtures with the same shapes)
* the test-suite (asserts on real parser input)

Generation is seeded: ``generate_log_line(scenario, seed=n)`` always returns the
same record for the same inputs, so demos and evaluations are reproducible.

Nothing here fabricates *analysis*: it fabricates only the workload under analysis,
which is exactly what the project specification asks for.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.faults import (
    DATABASE_TIMEOUT,
    DEPENDENCY_TIMEOUT,
    FAULT_SPECS,
    HTTP_500_BURST,
    INVALID_CONFIGURATION,
    LIVENESS_FAILURE,
    MEMORY_PRESSURE,
    FaultSpec,
    normalize_scenario,
)
from app.logging_config import utc_now_iso

DEFAULT_SERVICE = "checkout-service"

PATHS = ("/api/orders", "/api/orders/checkout", "/api/cart", "/api/products", "/health")
CLIENTS = ("web", "mobile", "partner-api")
REGIONS = ("ap-south-1", "eu-west-1", "us-east-1")


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def read_rss_bytes() -> int:
    """Real resident-set size of this process, in bytes (0 when unavailable)."""
    try:
        with open("/proc/self/statm", encoding="utf-8") as handle:
            pages = int(handle.read().split()[1])
        return pages * 4096
    except (OSError, IndexError, ValueError):
        return 0


# --- normal traffic -----------------------------------------------------------


def normal_record(seed: int, service: str = DEFAULT_SERVICE, timestamp: str | None = None) -> dict[str, Any]:
    rng = random.Random(f"normal-{seed}")
    path = PATHS[rng.randrange(len(PATHS))]
    duration = round(rng.uniform(0.008, 0.24), 4)
    return {
        "timestamp": timestamp or utc_now_iso(),
        "level": "INFO",
        "service": service,
        "logger": "app.request",
        "event": "request_completed",
        "message": f"request completed path={path}",
        "request_id": f"req-{seed:06d}",
        "path": path,
        "method": "GET" if path != "/api/orders/checkout" else "POST",
        "status": 200,
        "duration_ms": duration * 1000,
        "client": CLIENTS[rng.randrange(len(CLIENTS))],
    }


NOISE_TEMPLATES: tuple[Callable[[random.Random], dict[str, Any]], ...] = (
    lambda rng: {
        "level": "DEBUG",
        "logger": "app.cache",
        "event": "cache_hit",
        "message": "cache hit for key products:catalog",
        "key": "products:catalog",
        "ttl_seconds": 60,
    },
    lambda rng: {
        "level": "INFO",
        "logger": "app.runtime",
        "event": "gc_completed",
        "message": "garbage collection completed",
        "gc_generation": rng.choice([0, 1, 2]),
        "collected": rng.randint(3, 40),
    },
    lambda rng: {
        "level": "INFO",
        "logger": "kubelet.probe",
        "event": "health_probe",
        "message": "health probe succeeded",
        "probe": rng.choice(["liveness", "readiness"]),
        "status": 200,
    },
    lambda rng: {
        "level": "WARNING",
        "logger": "vendor.telemetry",
        "event": "telemetry_backpressure",
        "message": "telemetry exporter buffer at 78 percent, dropping non-critical spans",
        "buffer_percent": 78,
    },
    lambda rng: {
        "level": "INFO",
        "logger": "app.static",
        "event": "static_asset",
        "message": "static asset served /static/app.css",
        "path": "/static/app.css",
        "status": 200,
        "bytes": 18432,
    },
    lambda rng: {
        "level": "DEBUG",
        "logger": "app.db.pool",
        "event": "pool_stats",
        "message": "connection pool stats healthy",
        "pool_size": 10,
        "in_use": rng.randint(0, 3),
        "idle": rng.randint(1, 10),
    },
)

NON_JSON_NOISE = (
    "not a json line at all",
    "",
    "{ this is not valid json",
    "2026-09-26T10:00:00Z INFO some-legacy-component started up fine",
    '{"timestamp":"2026-09-26T10:00:00Z","level":"INFO"',  # truncated json
)


def noise_record(seed: int, service: str = DEFAULT_SERVICE, timestamp: str | None = None) -> dict[str, Any]:
    rng = random.Random(f"noise-{seed}")
    template = NOISE_TEMPLATES[seed % len(NOISE_TEMPLATES)]
    record = template(rng)
    record.update(
        {
            "timestamp": timestamp or utc_now_iso(),
            "service": service,
            "request_id": f"req-noise-{seed:05d}",
        }
    )
    return record


def non_json_noise_line(seed: int) -> str:
    """Return a raw, unparseable log line - used to prove parser resilience."""
    return NON_JSON_NOISE[seed % len(NON_JSON_NOISE)]


# --- failure scenarios --------------------------------------------------------


def _db_timeout_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "ERROR",
        "logger": "app.db",
        "event": "db_query_timeout",
        "message": "database query timed out after 5000ms while selecting pending orders",
        "error": {
            "type": DATABASE_TIMEOUT,
            "code": "57014",
            "detail": "canceling statement due to statement timeout",
            "elapsed_ms": 5000,
            "timeout_ms": 5000,
        },
        "db": {"system": "postgresql", "host": "postgres-primary", "statement": "SELECT * FROM orders"},
        "status": 503,
    }


def _db_pool_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "WARNING",
        "logger": "app.db.pool",
        "event": "db_pool_exhausted",
        "message": "database connection pool exhausted, waiting for a free connection",
        "error": {"type": DATABASE_TIMEOUT, "code": "POOL_WAIT"},
        "pool_size": 10,
        "in_use": 10,
        "waiters": rng.randint(4, 18),
    }


def _db_txn_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "ERROR",
        "logger": "app.checkout",
        "event": "checkout_failed",
        "message": "checkout failed: database transaction timed out",
        "error": {"type": DATABASE_TIMEOUT, "code": "57014"},
        "status": 503,
        "attempt": rng.randint(1, 3),
    }


def _memory_warning_record(rng: random.Random) -> dict[str, Any]:
    rss = read_rss_bytes()
    return {
        "level": "WARNING",
        "logger": "app.runtime",
        "event": "memory_high_watermark",
        "message": "memory usage above high watermark, heap growing steadily",
        "error": {"type": MEMORY_PRESSURE, "code": "MEM_HIGH_WATERMARK"},
        "rss_bytes": rss or rng.randint(280_000_000, 380_000_000),
        "heap_used_percent": rng.randint(86, 96),
    }


def _memory_alloc_record(rng: random.Random) -> dict[str, Any]:
    rss = read_rss_bytes()
    return {
        "level": "ERROR",
        "logger": "app.runtime",
        "event": "memory_allocation_failed",
        "message": "memory allocation failed, unable to grow heap for request buffer",
        "error": {
            "type": MEMORY_PRESSURE,
            "code": "MEMORY_ALLOCATION_FAILED",
            "detail": "std::bad_alloc: out of memory",
        },
        "rss_bytes": rss or rng.randint(360_000_000, 480_000_000),
        "requested_bytes": rng.randint(16_000_000, 96_000_000),
        "status": 500,
    }


def _memory_oom_record(rng: random.Random) -> dict[str, Any]:
    rss = read_rss_bytes()
    return {
        "level": "CRITICAL",
        "logger": "app.runtime",
        "event": "oomkill_risk",
        "message": "container approaching memory limit, OOMKill imminent",
        "error": {"type": MEMORY_PRESSURE, "code": "OOMKILL_RISK"},
        "rss_bytes": rss or rng.randint(420_000_000, 490_000_000),
        "memory_limit_bytes": 524_288_000,
    }


def _http500_exception_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "ERROR",
        "logger": "app.request",
        "event": "unhandled_exception",
        "message": "unhandled exception while processing request, returning HTTP 500",
        "error": {
            "type": HTTP_500_BURST,
            "code": "INTERNAL_SERVER_ERROR",
            "exception": "ValueError: invalid literal for int() with base 10: 'None'",
        },
        "status": 500,
        "path": rng.choice(["/api/orders/checkout", "/api/orders", "/api/cart"]),
        "traceback": "Traceback (most recent call last): ... ValueError",
    }


def _http500_response_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "ERROR",
        "logger": "app.request",
        "event": "http_500_response",
        "message": "request failed with HTTP 500 internal server error",
        "error": {"type": HTTP_500_BURST, "code": "INTERNAL_SERVER_ERROR"},
        "status": 500,
        "path": rng.choice(["/api/orders/checkout", "/api/orders", "/api/cart"]),
        "duration_ms": round(rng.uniform(4, 90), 2),
    }


def _dependency_timeout_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "ERROR",
        "logger": "app.dependency",
        "event": "dependency_request_timeout",
        "message": "downstream dependency call timed out after 3000ms calling payment-gateway",
        "error": {
            "type": DEPENDENCY_TIMEOUT,
            "code": "UPSTREAM_TIMEOUT",
            "detail": "read timed out waiting for payment-gateway response",
            "elapsed_ms": 3000,
            "timeout_ms": 3000,
        },
        "dependency": "payment-gateway",
        "upstream": "https://payment-gateway.internal/v1/charges",
        "status": 504,
    }


def _dependency_retry_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "WARNING",
        "logger": "app.dependency",
        "event": "dependency_retry",
        "message": "retrying dependency call to payment-gateway after timeout",
        "error": {"type": DEPENDENCY_TIMEOUT, "code": "UPSTREAM_TIMEOUT"},
        "dependency": "payment-gateway",
        "retry": rng.randint(1, 3),
    }


def _dependency_breaker_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "ERROR",
        "logger": "app.dependency",
        "event": "circuit_breaker_open",
        "message": "circuit breaker opened for payment-gateway after repeated timeouts",
        "error": {"type": DEPENDENCY_TIMEOUT, "code": "CIRCUIT_OPEN"},
        "dependency": "payment-gateway",
        "failure_count": rng.randint(5, 20),
        "status": 504,
    }


def _config_invalid_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "ERROR",
        "logger": "app.config",
        "event": "invalid_configuration",
        "message": "invalid configuration: CHECKOUT_RETRY_POLICY is not a valid integer",
        "error": {
            "type": INVALID_CONFIGURATION,
            "code": "INVALID_CONFIG_VALUE",
            "detail": "expected integer, received 'aggressive'",
        },
        "config_key": "CHECKOUT_RETRY_POLICY",
        "expected": "integer",
        "received": "aggressive",
        "status": 500,
    }


def _config_missing_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "CRITICAL",
        "logger": "app.config",
        "event": "configuration_missing",
        "message": "required configuration key CHECKOUT_PAYMENT_PROVIDER is missing",
        "error": {"type": INVALID_CONFIGURATION, "code": "CONFIG_KEY_MISSING"},
        "config_key": "CHECKOUT_PAYMENT_PROVIDER",
        "status": 500,
    }


def _config_reload_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "ERROR",
        "logger": "app.config",
        "event": "config_reload_rejected",
        "message": "configuration reload rejected: schema validation failed for CHECKOUT_RETRY_POLICY",
        "error": {"type": INVALID_CONFIGURATION, "code": "CONFIG_SCHEMA_INVALID"},
        "config_key": "CHECKOUT_RETRY_POLICY",
        "status": 500,
    }


def _liveness_record(rng: random.Random) -> dict[str, Any]:
    return {
        "level": "CRITICAL",
        "logger": "app.health",
        "event": "liveness_check_failed",
        "message": "liveness check failed: internal self-test did not complete",
        "error": {"type": LIVENESS_FAILURE, "code": "LIVENESS_FAILED"},
        "status": 503,
    }


SCENARIO_BUILDERS: dict[str, tuple[Callable[[random.Random], dict[str, Any]], ...]] = {
    DATABASE_TIMEOUT: (_db_timeout_record, _db_pool_record, _db_txn_record),
    MEMORY_PRESSURE: (_memory_warning_record, _memory_alloc_record, _memory_oom_record),
    HTTP_500_BURST: (_http500_exception_record, _http500_response_record),
    DEPENDENCY_TIMEOUT: (_dependency_timeout_record, _dependency_retry_record, _dependency_breaker_record),
    INVALID_CONFIGURATION: (_config_invalid_record, _config_missing_record, _config_reload_record),
    LIVENESS_FAILURE: (_liveness_record,),
}


def failure_record(
    ground_truth: str,
    seed: int,
    service: str = DEFAULT_SERVICE,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build one deterministic failure log record for a ground-truth label."""
    if ground_truth not in SCENARIO_BUILDERS:
        raise ValueError(f"unknown ground truth label {ground_truth!r}")
    builders = SCENARIO_BUILDERS[ground_truth]
    rng = random.Random(f"{ground_truth}-{seed}")
    record = builders[seed % len(builders)](rng)
    record["timestamp"] = timestamp or utc_now_iso()
    record["service"] = service
    record["request_id"] = f"req-{ground_truth[:4]}-{seed:05d}"
    return record


def request_error_response(spec: FaultSpec, seed: int) -> dict[str, Any]:
    """Body returned by the demo API endpoints while a fault is active."""
    return {
        "status": "error",
        "fault_injection": spec.name,
        "simulated": True,
        "detail": spec.description,
        "status_code": spec.http_status,
        "request_id": f"req-{seed:06d}",
    }


# --- scenario sequences -------------------------------------------------------


def scenario_log_lines(
    scenario: str,
    total: int = 120,
    error_ratio: float = 0.55,
    noise_ratio: float = 0.15,
    service: str = DEFAULT_SERVICE,
    start_epoch: float | None = None,
    step_seconds: float = 0.35,
    include_non_json: bool = True,
) -> list[str]:
    """Render a complete, deterministic log file body for one scenario.

    Interleaves failure records with normal traffic and irrelevant noise so the
    analyzer has to do real correlation work rather than reading a clean stream.
    """
    name = normalize_scenario(scenario)
    spec = FAULT_SPECS[name]
    base = start_epoch if start_epoch is not None else time.time() - (total * step_seconds)
    lines: list[str] = []
    error_budget = int(total * error_ratio)
    noise_budget = int(total * noise_ratio)
    for index in range(total):
        stamp = _iso(base + index * step_seconds)
        if index < error_budget:
            record = failure_record(spec.ground_truth, index, service=service, timestamp=stamp)
        elif index < error_budget + noise_budget:
            record = noise_record(index, service=service, timestamp=stamp)
        else:
            record = normal_record(index, service=service, timestamp=stamp)
        lines.append(json.dumps(record, separators=(",", ":"), default=str))
        if include_non_json and index % 17 == 16:
            lines.append(non_json_noise_line(index))
    return lines


def normal_log_lines(total: int = 120, service: str = DEFAULT_SERVICE, noise_ratio: float = 0.2) -> list[str]:
    """Render a deterministic healthy log file body (no injected failure)."""
    base = time.time() - total
    lines: list[str] = []
    noise_budget = int(total * noise_ratio)
    for index in range(total):
        stamp = _iso(base + index)
        if index < noise_budget:
            record = noise_record(index, service=service, timestamp=stamp)
        else:
            record = normal_record(index, service=service, timestamp=stamp)
        lines.append(json.dumps(record, separators=(",", ":"), default=str))
    return lines


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
