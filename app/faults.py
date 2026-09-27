"""Deterministic fault-injection registry.

All "failures" in this project are **simulations** for evaluation. Nothing here
deletes data, touches the host, runs shell commands or mutates infrastructure.
The only physically real effect available is the optional bounded memory
allocation used by ``memory-pressure`` (disabled by default, capped).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# Ground-truth labels are fixed by the project specification and must never be
# derived from, or adjusted to match, analyzer output.
DATABASE_TIMEOUT = "database_timeout"
MEMORY_PRESSURE = "memory_pressure"
HTTP_500_BURST = "http_500_burst"
DEPENDENCY_TIMEOUT = "dependency_timeout"
INVALID_CONFIGURATION = "invalid_configuration"
LIVENESS_FAILURE = "liveness_probe_failure"

SCENARIO_NAMES = (
    "database-timeout",
    "memory-pressure",
    "http-500-burst",
    "dependency-timeout",
    "invalid-configuration",
    "liveness-failure",
)


@dataclass(frozen=True)
class FaultSpec:
    """Static description of an injectable simulation."""

    name: str
    ground_truth: str
    title: str
    description: str
    error_rate: float
    logs_per_tick: int
    http_status: int
    runbook_id: str


FAULT_SPECS: dict[str, FaultSpec] = {
    "database-timeout": FaultSpec(
        name="database-timeout",
        ground_truth=DATABASE_TIMEOUT,
        title="Database timeout",
        description=(
            "Simulated PostgreSQL query/statement timeouts: connection pool exhaustion and "
            "failed checkouts with 503 responses."
        ),
        error_rate=0.6,
        logs_per_tick=3,
        http_status=503,
        runbook_id=DATABASE_TIMEOUT,
    ),
    "memory-pressure": FaultSpec(
        name="memory-pressure",
        ground_truth=MEMORY_PRESSURE,
        title="Memory pressure",
        description=(
            "Simulated memory leak / high RSS: heap growth warnings, allocation failures and "
            "OOMKill-risk errors."
        ),
        error_rate=0.5,
        logs_per_tick=3,
        http_status=500,
        runbook_id=MEMORY_PRESSURE,
    ),
    "http-500-burst": FaultSpec(
        name="http-500-burst",
        ground_truth=HTTP_500_BURST,
        title="HTTP 500 burst",
        description="Burst of unhandled application exceptions returning HTTP 500.",
        error_rate=0.95,
        logs_per_tick=4,
        http_status=500,
        runbook_id=HTTP_500_BURST,
    ),
    "dependency-timeout": FaultSpec(
        name="dependency-timeout",
        ground_truth=DEPENDENCY_TIMEOUT,
        title="Dependency timeout",
        description=(
            "Simulated downstream dependency (payment-gateway) timeouts, retries and circuit "
            "breaker opening with 504 responses."
        ),
        error_rate=0.7,
        logs_per_tick=3,
        http_status=504,
        runbook_id=DEPENDENCY_TIMEOUT,
    ),
    "invalid-configuration": FaultSpec(
        name="invalid-configuration",
        ground_truth=INVALID_CONFIGURATION,
        title="Invalid configuration",
        description="Deterministic missing/invalid configuration key detected at request time.",
        error_rate=1.0,
        logs_per_tick=3,
        http_status=500,
        runbook_id=INVALID_CONFIGURATION,
    ),
    "liveness-failure": FaultSpec(
        name="liveness-failure",
        ground_truth=LIVENESS_FAILURE,
        title="Liveness probe failure",
        description=(
            "Makes /health/live report unhealthy so Kubernetes restarts the pod. Used only to "
            "demonstrate probe behaviour; it is not one of the five scored evaluation scenarios."
        ),
        error_rate=1.0,
        logs_per_tick=1,
        http_status=503,
        runbook_id="liveness_probe_failure",
    ),
}

EVALUATION_SCENARIOS = tuple(
    name for name in SCENARIO_NAMES if name != "liveness-failure"
)

GROUND_TRUTH_BY_SCENARIO = {
    name: FAULT_SPECS[name].ground_truth for name in SCENARIO_NAMES
}


def normalize_scenario(raw: str) -> str:
    """Normalize a user-supplied scenario name; raises ValueError for unknown input.

    Accepts ``database-timeout``, ``database_timeout`` and ``Database Timeout``.
    """
    if not isinstance(raw, str):
        raise ValueError("scenario must be a string")
    candidate = raw.strip().lower().replace("_", "-").replace(" ", "-")
    if candidate in FAULT_SPECS:
        return candidate
    # tolerate the ground-truth style label as well (database_timeout -> database-timeout)
    label_to_name = {spec.ground_truth: name for name, spec in FAULT_SPECS.items()}
    if candidate in label_to_name:
        return label_to_name[candidate]
    raise ValueError(
        f"unknown failure scenario {raw!r}; valid options: {', '.join(SCENARIO_NAMES)}"
    )


@dataclass
class ActiveFault:
    """A currently active simulation with a hard expiry."""

    scenario: str
    ground_truth: str
    started_at: float
    expires_at: float
    injected_by: str = "api"
    injection_id: str = field(default_factory=lambda: f"inj-{uuid.uuid4().hex[:12]}")

    def is_active(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) < self.expires_at

    def remaining_seconds(self, now: float | None = None) -> float:
        return max(0.0, self.expires_at - (now if now is not None else time.time()))

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        return {
            "injection_id": self.injection_id,
            "scenario": self.scenario,
            "ground_truth": self.ground_truth,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "remaining_seconds": round(self.remaining_seconds(now), 2),
            "active": self.is_active(now),
            "injected_by": self.injected_by,
        }


class FaultInjector:
    """Thread-safe registry of active simulations.

    Deliberately minimal: one active fault at a time so evaluation stays
    deterministic and attributable to a single ground-truth label.
    """

    def __init__(self, default_duration_seconds: float = 20.0) -> None:
        self._lock = __import__("threading").Lock()
        self._active: ActiveFault | None = None
        self.default_duration_seconds = default_duration_seconds
        self.history: list[dict[str, Any]] = []
        # Held memory blocks for the optional real-allocation memory simulation.
        self._held_blocks: list[bytearray] = []

    # -- activation ------------------------------------------------------
    def activate(
        self,
        scenario: str,
        duration_seconds: float | None = None,
        injected_by: str = "api",
    ) -> ActiveFault:
        name = normalize_scenario(scenario)
        duration = float(duration_seconds if duration_seconds is not None else self.default_duration_seconds)
        if duration <= 0 or duration > 3600:
            raise ValueError("duration_seconds must be between 0 (exclusive) and 3600")
        now = time.time()
        fault = ActiveFault(
            scenario=name,
            ground_truth=FAULT_SPECS[name].ground_truth,
            started_at=now,
            expires_at=now + duration,
            injected_by=injected_by,
        )
        with self._lock:
            self._release_memory_locked()
            self._active = fault
            self.history.append(fault.to_dict(now))
        return fault

    def clear(self) -> dict[str, Any] | None:
        with self._lock:
            self._release_memory_locked()
            previous = self._active
            self._active = None
        return previous.to_dict() if previous else None

    def current(self, now: float | None = None) -> ActiveFault | None:
        with self._lock:
            fault = self._active
            if fault is None:
                return None
            if not fault.is_active(now):
                self._release_memory_locked()
                self._active = None
                return None
            return fault

    def current_ground_truth(self) -> str | None:
        fault = self.current()
        return fault.ground_truth if fault else None

    # -- optional real memory pressure -----------------------------------
    def hold_memory(self, megabytes: int, cap_megabytes: int) -> int:
        """Allocate a bounded amount of memory to make memory pressure physically real.

        Returns the number of megabytes actually held. Never exceeds ``cap_megabytes``.
        """
        if megabytes <= 0:
            return 0
        target = min(int(megabytes), max(1, int(cap_megabytes)))
        with self._lock:
            self._release_memory_locked()
            held = 0
            block_size = 8 * 1024 * 1024
            try:
                while held < target:
                    chunk = min(block_size, (target - held) * 1024 * 1024)
                    block = bytearray(chunk)
                    block[0] = 1  # touch the page so the RSS actually grows
                    self._held_blocks.append(block)
                    held += chunk // (1024 * 1024)
            except MemoryError:
                # Environment refused the allocation: keep the simulation log-only.
                pass
            return held

    def _release_memory_locked(self) -> None:
        self._held_blocks.clear()

    def held_megabytes(self) -> int:
        with self._lock:
            return sum(len(block) for block in self._held_blocks) // (1024 * 1024)

    # -- introspection ---------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        fault = self.current(now)
        return {
            "active": fault.to_dict(now) if fault else None,
            "known_scenarios": [
                {
                    "name": spec.name,
                    "ground_truth": spec.ground_truth,
                    "title": spec.title,
                    "description": spec.description,
                }
                for spec in FAULT_SPECS.values()
            ],
            "held_memory_megabytes": self.held_megabytes(),
            "history": list(self.history[-20:]),
        }

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), indent=2)
