"""Scheduled incident-analysis trigger.

A daemon thread that periodically analyzes the latest bounded log window. This is
one of the supported incident triggers (manual / scheduled / threshold). It is
disabled by default (ANALYZE_INTERVAL_SECONDS=0) so demos stay deterministic; enable
it to watch continuous analysis. It only *analyzes* - it never executes remediation.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from app.config import Settings

logger = logging.getLogger("app.scheduler")


class AnalysisScheduler:
    def __init__(self, settings: Settings, analyze_fn: Callable[[], None]) -> None:
        self.settings = settings
        self.analyze_fn = analyze_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_run_at: float | None = None
        self.run_count = 0

    def start(self) -> None:
        interval = self.settings.analyze_interval_seconds
        if interval <= 0:
            logger.info("scheduled analysis disabled (ANALYZE_INTERVAL_SECONDS=0)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="analysis-scheduler", daemon=True)
        self._thread.start()
        logger.info("scheduled analysis started (every %ss)", interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        interval = max(1, self.settings.analyze_interval_seconds)
        while not self._stop.is_set():
            self._stop.wait(interval)
            if self._stop.is_set():
                break
            try:
                self.analyze_fn()
                self.last_run_at = time.time()
                self.run_count += 1
            except Exception as exc:  # pragma: no cover - loop must never die
                logger.warning("scheduled analysis failed: %s", exc)

    def snapshot(self) -> dict[str, object]:
        return {
            "running": self.running,
            "interval_seconds": self.settings.analyze_interval_seconds,
            "run_count": self.run_count,
            "last_run_at": self.last_run_at,
        }
