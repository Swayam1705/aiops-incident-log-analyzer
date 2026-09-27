"""Shared pytest fixtures.

These build an isolated runtime per test (temp dirs, in-process metrics, no network)
so tests are deterministic and never touch the developer's real logs or Ollama.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the repo root is importable when pytest is run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.analyzer.notify import Notifier  # noqa: E402
from app.analyzer.pipeline import Analyzer  # noqa: E402
from app.analyzer.report import ReportStore  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.metrics import MetricsRegistry  # noqa: E402
from app.runtime import build_runtime  # noqa: E402


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "incidents").mkdir()
    return tmp_path


@pytest.fixture()
def settings(tmp_workspace: Path):
    return load_settings(
        env={
            "LLM_ENABLED": "false",
            "LOG_FILE": str(tmp_workspace / "logs" / "app.log"),
            "COLLECTOR_FILE_GLOB": str(tmp_workspace / "logs" / "*.log"),
            "INCIDENTS_DIR": str(tmp_workspace / "incidents"),
            "LOG_WINDOW_SIZE": "300",
            "ANALYZE_MIN_ERROR_LINES": "5",
            "LOG_LEVEL": "CRITICAL",
        },
        base_dir=tmp_workspace,
    )


@pytest.fixture()
def store(settings) -> ReportStore:
    return ReportStore(settings.resolve(settings.incidents_dir), retention_limit=50)


@pytest.fixture()
def metrics() -> MetricsRegistry:
    return MetricsRegistry()


@pytest.fixture()
def analyzer(settings, store, metrics) -> Analyzer:
    # LLM disabled, notifier unconfigured: pure deterministic path.
    return Analyzer(settings, store, llm=None, notifier=Notifier(""), metrics=metrics)


@pytest.fixture()
def runtime(settings, metrics):
    """A fully wired runtime. The simulator runs on a fast tick so the realistic
    inject -> logs -> analyze flow can be exercised over HTTP in the route tests."""
    env = {
        "LLM_ENABLED": "false",
        "LOG_FILE": str(settings.resolve(settings.log_file)),
        "COLLECTOR_FILE_GLOB": str(settings.resolve(settings.collector_file_glob)),
        "INCIDENTS_DIR": str(settings.resolve(settings.incidents_dir)),
        "LOG_WINDOW_SIZE": "300",
        "LOG_LEVEL": "CRITICAL",
        "SIMULATOR_ENABLED": "true",
        "SIMULATOR_INTERVAL_SECONDS": "0.05",
        "SIMULATOR_LOGS_PER_TICK": "3",
    }
    return build_runtime(env=env, base_dir=settings.base_dir, configure_logs=False, metrics=metrics)


@pytest.fixture()
def client(runtime):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(runtime)
    with TestClient(app) as test_client:
        yield test_client


def write_log_file(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n", encoding="utf-8")
    return path
