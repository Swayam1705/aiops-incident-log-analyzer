"""Background workload + failure simulator.

The simulator is the *workload under observation*. It emits structured log records
(normal traffic, and failure records while a fault is active) and updates the live
metrics so a reviewer can watch request/error/latency/incident signals move in
Grafana without needing external traffic.

It runs in a daemon thread, is deterministic (seeded per tick), and never performs
destructive actions. The only physical side effect is the optional bounded memory
allocation for ``memory-pressure`` (disabled unless configured, always capped).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any

from app.analyzer.report import new_incident_id
from app.config import Settings
from app.faults import FAULT_SPECS, FaultInjector
from app.log_generator import (
    failure_record,
    new_request_id,
    noise_record,
    normal_record,
    read_rss_bytes,
    request_error_response,
)
from app.logging_config import RotatingFileSink, write_structured
from app.metrics import MetricsRegistry

logger = logging.getLogger("app.simulator")


class WorkloadSimulator:
    def __init__(
        self,
        settings: Settings,
        sink: RotatingFileSink | None,
        metrics: MetricsRegistry,
        injector: FaultInjector,
        on_log: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.settings = settings
        self.sink = sink
        self.metrics = metrics
        self.injector = injector
        self.on_log = on_log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick = 0
        self._started_at = time.time()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if not self.settings.simulator_enabled:
            logger.info("workload simulator disabled by configuration")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="workload-simulator", daemon=True)
        self._thread.start()
        logger.info("workload simulator started (interval=%.2fs)", self.settings.simulator_interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.injector.clear()
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -- main loop -------------------------------------------------------
    def _run(self) -> None:
        interval = max(0.05, self.settings.simulator_interval_seconds)
        while not self._stop.is_set():
            try:
                self._emit_tick()
            except Exception as exc:  # pragma: no cover - loop must never die
                logger.warning("simulator tick failed: %s", exc)
            self._stop.wait(interval)

    def _emit_tick(self) -> None:
        self._tick += 1
        fault = self.injector.current()

        # Always update a real memory gauge so memory-pressure evidence is grounded.
        try:
            self.metrics.memory_rss_bytes.set(read_rss_bytes())
        except Exception:  # pragma: no cover
            pass

        # Optional real memory pressure while a memory-pressure fault is active.
        if fault and fault.scenario == "memory-pressure" and self.settings.memory_pressure_simulate_mb > 0:
            self.injector.hold_memory(
                self.settings.memory_pressure_simulate_mb, self.settings.memory_pressure_max_mb
            )

        per_tick = max(0, self.settings.simulator_logs_per_tick)
        for index in range(per_tick):
            seed = self._tick * 100 + index
            if fault:
                self._emit_fault_log(fault, seed)
            else:
                self._emit_normal_log(seed)

    # -- emission --------------------------------------------------------
    def _emit_normal_log(self, seed: int) -> None:
        rng = random.Random(f"tick-{seed}")
        # Mostly normal traffic, occasionally irrelevant noise.
        if rng.random() < 0.12:
            record = noise_record(seed, service=self.settings.app_name)
            level = record.get("level", "INFO")
        else:
            record = normal_record(seed, service=self.settings.app_name)
            level = "INFO"
            self._record_request(record, outcome="ok")
        self._write(record, level, record.get("event", "log"), record.get("message", ""))

    def _emit_fault_log(self, fault: Any, seed: int) -> None:
        spec = FAULT_SPECS[fault.scenario]
        record = failure_record(fault.ground_truth, seed, service=self.settings.app_name)
        level = record.get("level", "ERROR")
        self._write(record, level, record.get("event", "fault"), record.get("message", ""))
        # Count the simulated failing request so error metrics move.
        self.metrics.errors_total.inc(service=self.settings.app_name, error_type=fault.ground_truth)
        self._record_request(
            {
                "method": "POST",
                "path": "/api/orders/checkout",
                "duration_ms": 120.0,
            },
            outcome="error",
            status=spec.http_status,
        )

    def _record_request(self, record: dict[str, Any], outcome: str, status: int | None = None) -> None:
        method = record.get("method", "GET")
        path = record.get("path", "/api/orders")
        code = status if status is not None else int(record.get("status", 200))
        self.metrics.request_total.inc(method=method, path=path, status=str(code), outcome=outcome)
        try:
            self.metrics.request_latency.observe(
                float(record.get("duration_ms", 50.0)) / 1000.0,
                method=method,
                path=path,
                outcome=outcome,
            )
        except (TypeError, ValueError):
            pass

    def _write(self, record: dict[str, Any], level: str, event: str, message: str) -> None:
        # Persist through the structured sink (file) and emit to stdout logger.
        if self.sink is not None:
            self.sink.write_line(record)
        self.metrics.logs_total.inc(level=level.upper())
        log_fn = getattr(logger, level.lower(), logger.info)
        extra = {k: v for k, v in record.items() if k not in ("timestamp", "level", "message")}
        try:
            log_fn(message, extra=extra)
        except Exception:  # pragma: no cover - logging must never break the loop
            pass
        if self.on_log is not None:
            try:
                self.on_log(record)
            except Exception:  # pragma: no cover
                pass

    # -- helpers used by routes -----------------------------------------
    def make_request_error(self, fault: Any) -> dict[str, Any]:
        spec = FAULT_SPECS[fault.scenario]
        return request_error_response(spec, self._tick)

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "tick": self._tick,
            "uptime_seconds": round(time.time() - self._started_at, 2),
            "interval_seconds": self.settings.simulator_interval_seconds,
            "logs_per_tick": self.settings.simulator_logs_per_tick,
        }


# Re-export for convenience (used by tests to assert on generated request ids).
__all__ = ["WorkloadSimulator", "new_request_id", "new_incident_id", "write_structured"]
