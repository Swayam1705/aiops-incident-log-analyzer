"""FastAPI application entrypoint.

Builds the wired :class:`~app.runtime.Runtime`, starts the background workload
simulator and (optionally) the analysis scheduler, mounts the JSON API and the UI,
and cleanly shuts everything down. Run with::

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.routes import router as api_router
from app.runtime import Runtime, build_runtime
from app.scheduler import AnalysisScheduler
from app.simulator import WorkloadSimulator
from app.ui_routes import UI_DIR, ui_router

logger = logging.getLogger("app.main")


def _scheduled_analysis(runtime: Runtime) -> None:
    """Callback used by the scheduler: analyze the latest bounded window."""
    runtime.analyzer.analyze_files(trigger="scheduled")


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime: Runtime = app.state.runtime
    simulator = WorkloadSimulator(
        runtime.settings, runtime.sink, runtime.metrics, runtime.injector
    )
    scheduler = AnalysisScheduler(runtime.settings, lambda: _scheduled_analysis(runtime))
    app.state.simulator = simulator
    app.state.scheduler = scheduler
    app.state.last_evaluation = None

    simulator.start()
    scheduler.start()
    logger.info(
        "aiops-incident-log-analyzer started (llm=%s, model=%s)",
        "enabled" if runtime.settings.llm_requested else "disabled",
        runtime.settings.ollama_model,
    )
    try:
        yield
    finally:
        scheduler.stop()
        simulator.stop()
        logger.info("shutdown complete")


def create_app(runtime: Runtime | None = None) -> FastAPI:
    app = FastAPI(
        title="AIOps Incident & Log Analyzer",
        version=__version__,
        description="Local, zero-cost, AI-assisted incident analysis on Kubernetes.",
        lifespan=lifespan,
    )
    app.state.runtime = runtime or build_runtime()

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware, metrics=app.state.runtime.metrics)

    app.include_router(api_router)
    app.include_router(ui_router)

    static_dir = UI_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()


def main() -> None:  # pragma: no cover - console entrypoint
    import uvicorn

    from app.config import load_settings

    settings = load_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
