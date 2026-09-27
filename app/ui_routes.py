"""Server-rendered UI routes (Jinja2) + static assets.

The UI is a thin, professional read/act layer over the same JSON API the API clients
use. It renders real state (incidents, evaluation, health) and refreshes via fetch.
It executes nothing: all actions call backend endpoints that enforce safety.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.analyzer.evaluation import EvaluationReport

UI_DIR = Path(__file__).parent / "ui"
templates = Jinja2Templates(directory=str(UI_DIR / "templates"))

ui_router = APIRouter(prefix="/ui", tags=["ui"])


def _context(request: Request, **extra) -> dict:
    runtime = request.app.state.runtime
    simulator = getattr(request.app.state, "simulator", None)
    base = {
        "request": request,
        "app_name": runtime.settings.app_name,
        "llm_model": runtime.settings.ollama_model,
        "llm_enabled": runtime.settings.llm_requested,
        "active_fault": runtime.injector.current().to_dict() if runtime.injector.current() else None,
        "simulator_running": simulator.running if simulator else False,
    }
    base.update(extra)
    return base


@ui_router.get("/", response_class=HTMLResponse)
def overview(request: Request) -> HTMLResponse:
    runtime = request.app.state.runtime
    incidents = runtime.store.list()[:8]
    llm_ok, llm_detail = runtime.llm.available()
    return templates.TemplateResponse(
        request, "overview.html", _context(
            request,
            page="overview",
            incidents=incidents,
            incident_count=len(runtime.store.list()),
            llm_available=llm_ok,
            llm_detail=llm_detail,
            notification_configured=runtime.notifier.configured,
        ),
    )


@ui_router.get("/incidents", response_class=HTMLResponse)
def incidents_page(request: Request) -> HTMLResponse:
    runtime = request.app.state.runtime
    return templates.TemplateResponse(
        request, "incidents.html", _context(request, page="incidents", incidents=runtime.store.list())
    )


@ui_router.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_detail(request: Request, incident_id: str) -> HTMLResponse:
    runtime = request.app.state.runtime
    data = runtime.store.get(incident_id)
    return templates.TemplateResponse(
        request,
        "incident_detail.html",
        _context(request, page="incidents", incident=data, incident_id=incident_id),
    )


@ui_router.get("/evaluation", response_class=HTMLResponse)
def evaluation_page(request: Request) -> HTMLResponse:
    report: EvaluationReport | None = getattr(request.app.state, "last_evaluation", None)
    return templates.TemplateResponse(
        request, "evaluation.html", _context(request, page="evaluation", has_report=report is not None)
    )


@ui_router.get("/health", response_class=HTMLResponse)
def health_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "health.html", _context(request, page="health"))
