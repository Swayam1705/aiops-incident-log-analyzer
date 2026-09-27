"""Advisory runbooks / remediation guidance.

Every entry is **documentation for a human operator**. This module never executes
anything: no shell, no kubectl, no docker. Commands appear only as quoted text a
human may choose to run after validating the diagnosis. The LLM cannot add to or
trigger these.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Runbook:
    runbook_id: str
    title: str
    cause: str
    summary: str
    steps: tuple[str, ...]
    diagnostic_commands: tuple[str, ...] = field(default_factory=tuple)
    prevention: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "runbook_id": self.runbook_id,
            "title": self.title,
            "cause": self.cause,
            "summary": self.summary,
            "steps": list(self.steps),
            "diagnostic_commands": list(self.diagnostic_commands),
            "prevention": list(self.prevention),
            "advisory": True,
            "auto_executed": False,
        }


RUNBOOKS: dict[str, Runbook] = {
    "database_timeout": Runbook(
        runbook_id="database_timeout",
        title="Database timeout",
        cause="database_timeout",
        summary="Database queries or connection acquisition are timing out.",
        steps=(
            "Confirm the database is reachable from the application pods.",
            "Inspect the connection pool: size, in-use connections and waiters.",
            "Check for long-running or blocked queries and lock contention.",
            "Review statement_timeout and pool configuration against workload.",
        ),
        diagnostic_commands=(
            "kubectl -n aiops logs deploy/aiops-analyzer --tail=100",
            "kubectl -n aiops exec deploy/aiops-analyzer -- python -c 'print(\"check db config\")'",
            "# review DB metrics in Grafana: connection pool saturation, query latency",
        ),
        prevention=(
            "Right-size the connection pool and set sane statement timeouts.",
            "Add query latency SLO alerts before saturation becomes an outage.",
        ),
    ),
    "memory_pressure": Runbook(
        runbook_id="memory_pressure",
        title="Memory pressure",
        cause="memory_pressure",
        summary="Memory usage is high; allocations are failing and OOMKill is a risk.",
        steps=(
            "Inspect current memory utilization versus the container limit.",
            "Look for a leak or an unbounded in-memory structure.",
            "Verify resource requests/limits are appropriate for the workload.",
            "Consider a temporary rollout/restart to relieve pressure while investigating.",
        ),
        diagnostic_commands=(
            "kubectl -n aiops top pods",
            "kubectl -n aiops describe pod -l app.kubernetes.io/name=aiops-analyzer",
            "# review app_memory_rss_bytes in Grafana over the incident window",
        ),
        prevention=(
            "Set memory limits with headroom and alert on sustained high RSS.",
            "Profile allocations and cap in-memory caches.",
        ),
    ),
    "http_500_burst": Runbook(
        runbook_id="http_500_burst",
        title="HTTP 500 burst",
        cause="http_500_burst",
        summary="A burst of unhandled application errors is returning HTTP 500.",
        steps=(
            "Read recent application logs for the exception type and stack trace.",
            "Correlate with the most recent deploy or configuration change.",
            "Identify the failing endpoint(s) and affected request IDs.",
            "Confirm whether the error is systemic or input-specific.",
        ),
        diagnostic_commands=(
            "kubectl -n aiops logs deploy/aiops-analyzer --tail=200 | grep unhandled_exception",
            "# review http_requests_total{status=\"500\"} rate in Grafana",
        ),
        prevention=(
            "Add regression tests for the failing code path.",
            "Improve error handling so failures return structured, non-500 responses where possible.",
        ),
    ),
    "dependency_timeout": Runbook(
        runbook_id="dependency_timeout",
        title="Dependency timeout",
        cause="dependency_timeout",
        summary="A downstream dependency is timing out; retries and circuit breaking engaged.",
        steps=(
            "Confirm the dependency's availability and latency from the app's perspective.",
            "Inspect timeout, retry and circuit-breaker configuration.",
            "Check for network/DNS issues between the app and the dependency.",
            "Verify the dependency is not itself degraded.",
        ),
        diagnostic_commands=(
            "kubectl -n aiops logs deploy/aiops-analyzer --tail=200 | grep dependency",
            "# review circuit breaker state and dependency latency in Grafana",
        ),
        prevention=(
            "Set per-dependency timeouts and bounded retries with backoff.",
            "Add dependency health checks and fallbacks.",
        ),
    ),
    "invalid_configuration": Runbook(
        runbook_id="invalid_configuration",
        title="Invalid configuration",
        cause="invalid_configuration",
        summary="A required configuration value is missing or invalid.",
        steps=(
            "Identify the offending configuration key from the evidence.",
            "Compare the running ConfigMap/Secret against the expected schema.",
            "Correct the value and roll the deployment.",
            "Verify the service becomes healthy after the change.",
        ),
        diagnostic_commands=(
            "kubectl -n aiops get configmap aiops-analyzer-config -o yaml",
            "kubectl -n aiops rollout restart deploy/aiops-analyzer",
        ),
        prevention=(
            "Validate configuration at startup and fail fast with a clear message.",
            "Manage configuration as code with schema validation in CI.",
        ),
    ),
    "liveness_probe_failure": Runbook(
        runbook_id="liveness_probe_failure",
        title="Liveness probe failure",
        cause="liveness_probe_failure",
        summary="The liveness probe is failing; Kubernetes will restart the pod.",
        steps=(
            "Inspect the liveness endpoint response and application self-test.",
            "Check whether the failure is transient (startup) or persistent.",
            "Review probe timing (initialDelay, timeout, failureThreshold).",
        ),
        diagnostic_commands=(
            "kubectl -n aiops describe pod -l app.kubernetes.io/name=aiops-analyzer",
            "kubectl -n aiops get events --sort-by=.lastTimestamp | tail",
        ),
        prevention=("Tune probe thresholds to avoid killing slow-starting pods.",),
    ),
    "unknown": Runbook(
        runbook_id="unknown",
        title="Unknown / insufficient evidence",
        cause="unknown",
        summary="The analyzer could not determine a specific root cause from the window.",
        steps=(
            "Widen the log window (LOG_WINDOW_SIZE) and re-run analysis.",
            "Check whether the relevant service is emitting structured logs.",
            "Collect more evidence before acting; do not guess a remediation.",
        ),
        diagnostic_commands=("python scripts/run_analyzer.py --window-size 4000",),
        prevention=("Ensure all services emit structured, correlated logs.",),
    ),
}


def get_runbook(runbook_id: str | None) -> Runbook | None:
    if not runbook_id:
        return None
    return RUNBOOKS.get(runbook_id)


def recommended_action_for(runbook_id: str | None) -> str:
    """Short advisory action string used in the incident report headline."""
    runbook = get_runbook(runbook_id)
    if runbook is None:
        return (
            "Collect more evidence; the analyzer could not determine a specific cause. "
            "Do not act on a guess."
        )
    first = runbook.steps[0] if runbook.steps else "Investigate."
    return f"{runbook.title}: {first} (advisory - validate before acting)"


def list_runbooks() -> list[dict[str, object]]:
    return [runbook.to_dict() for runbook in RUNBOOKS.values()]
