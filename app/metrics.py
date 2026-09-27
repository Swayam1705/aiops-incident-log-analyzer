"""A tiny Prometheus exposition registry (stdlib only).

Deliberately hand-rolled instead of pulling in ``prometheus_client`` so the runtime
image stays small and the metric names/labels stay explicit and reviewable. The
output is valid Prometheus text exposition format (v0.0.4) and is scraped by the
Prometheus config in ``observability/prometheus.yml``.

All counters/gauges are real: they move when traffic, errors and analyses happen.
There are no constant placeholder series.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Iterable

DEFAULT_BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _escape_label(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _render_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{_escape_label(v)}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...]) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names

    def _check(self, labels: dict[str, str]) -> None:
        if set(labels) != set(self.label_names):
            raise ValueError(
                f"metric {self.name} expects labels {self.label_names}, got {sorted(labels)}"
            )


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_text: str, label_names: Iterable[str] = ()) -> None:
        super().__init__(name, help_text, tuple(label_names))
        self._values: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def labels(self, **labels: str) -> Counter:
        self._check(labels)
        return _BoundCounter(self, tuple(labels[key] for key in self.label_names), labels)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if labels:
            self.labels(**labels).inc(amount)
            return
        if amount < 0:
            raise ValueError("counter can only increase")
        key = tuple(labels.get(name, "") for name in self.label_names)
        with self._lock:
            self._values[key] += amount

    def value(self, **labels: str) -> float:
        key = tuple(labels.get(name, "") for name in self.label_names)
        with self._lock:
            return self._values.get(key, 0.0)

    def _snapshot(self) -> list[tuple[str, dict[str, str], float]]:
        with self._lock:
            items = list(self._values.items())
        out = []
        for key, value in items:
            out.append((self.name, dict(zip(self.label_names, key, strict=True)), value))
        return out


class _BoundCounter:
    def __init__(self, parent: Counter, key: tuple[str, ...], labels: dict[str, str]) -> None:
        self._parent = parent
        self._key = key
        self._labels = labels

    def inc(self, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("counter can only increase")
        with self._parent._lock:  # noqa: SLF001
            self._parent._values[self._key] += amount  # noqa: SLF001

    def value(self) -> float:
        with self._parent._lock:  # noqa: SLF001
            return self._parent._values.get(self._key, 0.0)  # noqa: SLF001


class Gauge(_Metric):
    kind = "gauge"

    def __init__(self, name: str, help_text: str, label_names: Iterable[str] = ()) -> None:
        super().__init__(name, help_text, tuple(label_names))
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels: str) -> None:  # noqa: A003 - Prometheus API name
        self._check(labels)
        key = tuple(labels[name] for name in self.label_names)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        self._check(labels)
        key = tuple(labels[name] for name in self.label_names)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def value(self, **labels: str) -> float:
        key = tuple(labels.get(name, "") for name in self.label_names)
        with self._lock:
            return self._values.get(key, 0.0)

    def _snapshot(self) -> list[tuple[str, dict[str, str], float]]:
        with self._lock:
            items = list(self._values.items())
        return [(self.name, dict(zip(self.label_names, key, strict=True)), value) for key, value in items]


class Histogram(_Metric):
    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Iterable[str] = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, tuple(label_names))
        self.buckets = tuple(sorted(buckets))
        self._counts: dict[tuple[str, ...], list[int]] = defaultdict(
            lambda: [0] * (len(self.buckets) + 1)
        )
        self._sums: dict[tuple[str, ...], float] = defaultdict(float)
        self._totals: dict[tuple[str, ...], int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        self._check(labels)
        key = tuple(labels[name] for name in self.label_names)
        with self._lock:
            counts = self._counts[key]
            for index, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[index] += 1
            counts[-1] += 1  # +Inf bucket
            self._sums[key] += value
            self._totals[key] += 1

    def count(self, **labels: str) -> int:
        key = tuple(labels.get(name, "") for name in self.label_names)
        with self._lock:
            return self._totals.get(key, 0)

    def total(self, **labels: str) -> float:
        key = tuple(labels.get(name, "") for name in self.label_names)
        with self._lock:
            return self._sums.get(key, 0.0)

    def _snapshot(self) -> list[tuple[str, dict[str, str], float]]:
        raise NotImplementedError("histograms are rendered directly")


class MetricsRegistry:
    """Owns the application's metrics and renders Prometheus text format."""

    def __init__(self) -> None:
        self.request_total = Counter(
            "http_requests_total",
            "Total HTTP requests handled by the application.",
            ("method", "path", "status", "outcome"),
        )
        self.request_latency = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds.",
            ("method", "path", "outcome"),
        )
        self.errors_total = Counter(
            "app_errors_total",
            "Application-level errors by error type and service.",
            ("service", "error_type"),
        )
        self.logs_total = Counter(
            "app_logs_total",
            "Structured log records generated, by level.",
            ("level",),
        )
        self.analysis_total = Counter(
            "analyzer_runs_total",
            "Analyzer runs by trigger and outcome.",
            ("trigger", "outcome"),
        )
        self.incidents_total = Counter(
            "incidents_total",
            "Incidents created by severity, root cause and analysis source.",
            ("severity", "root_cause", "analysis_source"),
        )
        self.incident_validations_total = Counter(
            "incident_validations_total",
            "Human validation decisions recorded for incidents.",
            ("decision",),
        )
        self.llm_calls_total = Counter(
            "llm_calls_total",
            "LLM analysis attempts by outcome (ok/validation_error/timeout/unavailable/disabled).",
            ("outcome",),
        )
        self.notifications_total = Counter(
            "notifications_total",
            "Incident notification deliveries by result.",
            ("result",),
        )
        self.log_lines_collected = Gauge(
            "analyzer_log_lines_collected",
            "Log lines collected in the most recent analysis window.",
        )
        self.log_lines_parsed = Gauge(
            "analyzer_log_lines_parsed",
            "Log lines successfully parsed in the most recent analysis window.",
        )
        self.log_parse_errors = Gauge(
            "analyzer_log_parse_errors",
            "Unparseable/noisy log lines skipped in the most recent analysis window.",
        )
        self.active_faults = Gauge(
            "app_active_fault_injections",
            "Number of currently active simulated fault injections.",
            ("scenario",),
        )
        self.memory_rss_bytes = Gauge(
            "app_memory_rss_bytes",
            "Resident set size of the process in bytes (used for memory-pressure evidence).",
        )
        self.info = Gauge(
            "app_info",
            "Build/runtime information labels.",
            ("service", "version", "env"),
        )
        self._metrics: list[_Metric] = [
            self.request_total,
            self.request_latency,
            self.errors_total,
            self.logs_total,
            self.analysis_total,
            self.incidents_total,
            self.incident_validations_total,
            self.llm_calls_total,
            self.notifications_total,
            self.log_lines_collected,
            self.log_lines_parsed,
            self.log_parse_errors,
            self.active_faults,
            self.memory_rss_bytes,
            self.info,
        ]

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        lines: list[str] = []
        for metric in self._metrics:
            lines.append(f"# HELP {metric.name} {metric.help_text}")
            lines.append(f"# TYPE {metric.name} {metric.kind}")
            if isinstance(metric, Histogram):
                with metric._lock:  # noqa: SLF001
                    keys = list(metric._counts.keys())  # noqa: SLF001
                    for key in keys:
                        labels = dict(zip(metric.label_names, key, strict=True))
                        counts = metric._counts[key]  # noqa: SLF001
                        cumulative = 0
                        for index, bound in enumerate(metric.buckets):
                            cumulative += counts[index]
                            bucket_labels = {**labels, "le": _format_float(bound)}
                            lines.append(
                                f"{metric.name}_bucket{_render_labels(bucket_labels)} {cumulative}"
                            )
                        inf_labels = {**labels, "le": "+Inf"}
                        total = counts[-1]
                        lines.append(f"{metric.name}_bucket{_render_labels(inf_labels)} {total}")
                        lines.append(f"{metric.name}_sum{_render_labels(labels)} {metric._sums[key]}")  # noqa: SLF001
                        lines.append(f"{metric.name}_count{_render_labels(labels)} {total}")
                continue
            for name, labels, value in metric._snapshot():  # noqa: SLF001
                lines.append(f"{name}{_render_labels(labels)} {_format_float(value)}")
        return "\n".join(lines) + "\n"

    def describe(self) -> dict[str, list[str]]:
        """Machine-readable summary of registered metric families (used by /health and tests)."""
        return {
            metric.name: [metric.kind, metric.help_text, *list(metric.label_names)]
            for metric in self._metrics
        }


def _format_float(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


REGISTRY = MetricsRegistry()
