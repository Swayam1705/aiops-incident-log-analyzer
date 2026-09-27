"""Single wiring point for the whole application.

:class:`Runtime` builds the settings, metrics registry, report store, Ollama client,
notifier, analyzer and fault injector once and shares them between the FastAPI app
and the CLI scripts, so there is exactly one definition of how the system is wired.

Nothing here starts background threads; the web layer owns lifecycle. Scripts build
a :class:`Runtime` and call the analyzer directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.analyzer.llm_client import OllamaClient
from app.analyzer.notify import Notifier
from app.analyzer.pipeline import Analyzer
from app.analyzer.report import ReportStore
from app.config import Settings, load_settings
from app.faults import FaultInjector
from app.logging_config import RotatingFileSink, configure_logging
from app.metrics import REGISTRY, MetricsRegistry

logger = logging.getLogger("app.runtime")


@dataclass
class Runtime:
    settings: Settings
    metrics: MetricsRegistry
    store: ReportStore
    llm: OllamaClient
    notifier: Notifier
    analyzer: Analyzer
    injector: FaultInjector
    sink: RotatingFileSink | None

    @property
    def incidents_dir(self) -> Path:
        return self.settings.resolve(self.settings.incidents_dir)


def build_runtime(
    env: dict[str, str] | None = None,
    base_dir: Path | str | None = None,
    *,
    configure_logs: bool = True,
    metrics: MetricsRegistry | None = None,
) -> Runtime:
    """Construct the fully wired runtime."""
    settings = load_settings(env=env, base_dir=base_dir)

    # The file sink is the durable log the analyzer reads. It is created whenever the
    # log path is usable, independently of whether stdout logging is configured, so
    # the simulator can always write and the analyzer can always collect.
    sink: RotatingFileSink | None = None
    try:
        sink = RotatingFileSink(settings.resolve(settings.log_file), settings.log_max_bytes)
    except OSError:
        sink = None

    if configure_logs:
        configure_logging(
            service=settings.app_name,
            level=settings.log_level,
            log_file=None,  # stdout only here; the file sink is handled separately
            log_format=settings.log_format,
            max_bytes=settings.log_max_bytes,
        )
    else:
        try:
            settings.resolve(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    registry = metrics or REGISTRY
    registry.info.set(1, service=settings.app_name, version="1.0.0", env=settings.env)

    store = ReportStore(
        settings.resolve(settings.incidents_dir),
        retention_limit=settings.report_retention_limit,
    )
    llm = OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_context_lines=settings.llm_max_context_lines,
        max_response_chars=settings.llm_max_response_chars,
        temperature=settings.llm_temperature,
        enabled=settings.llm_requested,
    )
    notifier = Notifier(
        webhook_url=settings.notification_webhook_url,
        timeout_seconds=settings.notification_timeout_seconds,
    )
    analyzer = Analyzer(settings, store, llm=llm, notifier=notifier, metrics=registry)
    injector = FaultInjector(default_duration_seconds=settings.fault_duration_seconds)

    return Runtime(
        settings=settings,
        metrics=registry,
        store=store,
        llm=llm,
        notifier=notifier,
        analyzer=analyzer,
        injector=injector,
        sink=sink,
    )
