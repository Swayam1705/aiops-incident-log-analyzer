"""HTTP API routes.

Every route returns real system state - there are no hard-coded fake results. The
LLM is advisory and unreachable from here as an operator: no route executes shell,
kubectl or remediation. Admin (fault-injection) routes can be token-gated.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from app.analyzer import collector as collector_mod
from app.analyzer import parser as parser_mod
from app.analyzer.evaluation import EvaluationReport, run_evaluation
from app.analyzer.report import VALIDATION_STATES
from app.analyzer.runbook import list_runbooks
from app.faults import EVALUATION_SCENARIOS, FAULT_SPECS, SCENARIO_NAMES, normalize_scenario
from app.log_generator import read_rss_bytes

logger = logging.getLogger("app.routes")

router = APIRouter()


# --- request models ----------------------------------------------------------


class AnalyzeRequest(BaseModel):
    window_size: int | None = Field(default=None, ge=1, le=20000)
    trigger: str = Field(default="manual")
    force_incident: bool = Field(default=False)
    ground_truth: str | None = Field(default=None)


class FaultRequest(BaseModel):
    scenario: str
    duration_seconds: float | None = Field(default=None, ge=1, le=3600)


class ValidateRequest(BaseModel):
    status: str
    validated_by: str | None = None
    note: str | None = None


# --- dependencies ------------------------------------------------------------


def get_runtime(request: Request):
    return request.app.state.runtime


def get_simulator(request: Request):
    return getattr(request.app.state, "simulator", None)


def require_admin(request: Request) -> None:
    """Gate admin routes when ADMIN_TOKEN is configured.

    If no token is configured the endpoint is open (local development default);
    this is documented as a limitation, not a claim of production auth.
    """
    runtime = request.app.state.runtime
    expected = runtime.settings.admin_token
    if not expected:
        return
    provided = request.headers.get("x-admin-token", "")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-Admin-Token")


# --- health & metrics --------------------------------------------------------


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui")


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    runtime = get_runtime(request)
    simulator = get_simulator(request)
    scheduler = getattr(request.app.state, "scheduler", None)
    llm_ok, llm_reason = runtime.llm.available()
    return {
        "status": "ok",
        "app": runtime.settings.app_name,
        "version": "1.0.0",
        "config": runtime.settings.to_public_dict(),
        "components": {
            "analyzer": "ready",
            "report_store": str(runtime.store.directory),
            "incident_count": len(runtime.store.list()),
            "ollama": {"available": llm_ok, "detail": llm_reason, "model": runtime.settings.ollama_model},
            "notification": {"configured": runtime.notifier.configured},
            "simulator": simulator.snapshot() if simulator else {"running": False},
            "scheduler": scheduler.snapshot() if scheduler else {"running": False},
            "active_fault": runtime.injector.current().to_dict() if runtime.injector.current() else None,
        },
        "metrics": runtime.metrics.describe(),
    }


@router.get("/health/live")
def liveness(request: Request) -> Response:
    """Liveness: unhealthy only when a liveness-failure fault is active."""
    runtime = get_runtime(request)
    fault = runtime.injector.current()
    if fault and fault.scenario == "liveness-failure":
        return JSONResponse({"status": "unhealthy", "reason": "liveness self-test failing"}, status_code=503)
    return JSONResponse({"status": "healthy"})


@router.get("/health/ready")
def readiness(request: Request) -> Response:
    """Readiness: unhealthy for misconfiguration or liveness failure."""
    runtime = get_runtime(request)
    fault = runtime.injector.current()
    if fault and fault.scenario in {"invalid-configuration", "liveness-failure"}:
        return JSONResponse(
            {"status": "not-ready", "reason": f"active fault: {fault.scenario}"}, status_code=503
        )
    return JSONResponse({"status": "ready"})


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request) -> Response:
    runtime = get_runtime(request)
    # Refresh a couple of live gauges at scrape time.
    try:
        runtime.metrics.memory_rss_bytes.set(read_rss_bytes())
        active = runtime.injector.current()
        for name in SCENARIO_NAMES:
            runtime.metrics.active_faults.set(
                1 if (active and active.scenario == name) else 0, scenario=name
            )
    except Exception:  # pragma: no cover - metrics must never break the endpoint
        pass
    return PlainTextResponse(runtime.metrics.render(), media_type="text/plain; version=0.0.4")


# --- logs --------------------------------------------------------------------


@router.get("/logs")
def recent_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=2000),
    severity: str | None = Query(default=None),
) -> dict[str, Any]:
    runtime = get_runtime(request)
    paths = runtime.settings.log_files()
    collection = collector_mod.collect_lines(
        paths, window_size=limit, max_bytes_per_file=runtime.settings.collector_max_bytes
    )
    records = parser_mod.parse_lines(collection.lines, source="file")
    if severity:
        wanted = severity.upper()
        records = [r for r in records if (r.severity or "").upper() == wanted]
    records = records[-limit:]
    return {
        "count": len(records),
        "files": collection.files_read,
        "truncated": collection.truncated,
        "errors": collection.errors,
        "records": [
            {
                "timestamp": r.timestamp,
                "level": r.severity,
                "service": r.service,
                "event": r.event,
                "message": r.message,
                "error_pattern": r.error_pattern,
                "request_id": r.request_id,
                "parsed": r.parsed,
                "classification": r.classification,
            }
            for r in records
        ],
    }


# --- analysis ----------------------------------------------------------------


@router.post("/analyze")
def analyze(request: Request, payload: AnalyzeRequest | None = Body(default=None)) -> dict[str, Any]:
    runtime = get_runtime(request)
    if not runtime.settings.analyze_enabled:
        raise HTTPException(status_code=503, detail="analysis disabled (ANALYZE_ENABLED=false)")
    payload = payload or AnalyzeRequest()
    if payload.window_size:
        original = runtime.settings.log_window_size
        runtime.settings.log_window_size = payload.window_size
        try:
            outcome = runtime.analyzer.analyze_files(
                trigger=payload.trigger,
                ground_truth=payload.ground_truth,
                force_incident=payload.force_incident,
            )
        finally:
            runtime.settings.log_window_size = original
    else:
        outcome = runtime.analyzer.analyze_files(
            trigger=payload.trigger,
            ground_truth=payload.ground_truth,
            force_incident=payload.force_incident,
        )
    result = outcome.summary_dict()
    if outcome.incident is not None:
        result["incident"] = outcome.incident.to_dict()
    return result


# --- incidents ---------------------------------------------------------------


@router.get("/incidents")
def list_incidents(request: Request) -> dict[str, Any]:
    runtime = get_runtime(request)
    return {"count": len(runtime.store.list()), "incidents": runtime.store.list()}


@router.get("/incidents/{incident_id}")
def get_incident(request: Request, incident_id: str, format: str = Query(default="json")) -> Response:
    runtime = get_runtime(request)
    if format == "md":
        markdown = runtime.store.get_markdown(incident_id)
        if markdown is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return PlainTextResponse(markdown, media_type="text/markdown")
    data = runtime.store.get(incident_id)
    if data is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return JSONResponse(data)


@router.post("/incidents/{incident_id}/validate")
def validate_incident(request: Request, incident_id: str, payload: ValidateRequest) -> dict[str, Any]:
    runtime = get_runtime(request)
    if payload.status not in VALIDATION_STATES:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {list(VALIDATION_STATES)}"
        )
    updated = runtime.store.set_validation(
        incident_id, payload.status, validated_by=payload.validated_by, note=payload.note
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="incident not found")
    try:
        runtime.metrics.incident_validations_total.inc(decision=payload.status)
    except Exception:  # pragma: no cover
        pass
    return updated


# --- runbooks ----------------------------------------------------------------


@router.get("/runbooks")
def runbooks() -> dict[str, Any]:
    items = list_runbooks()
    return {"count": len(items), "advisory": True, "auto_executed": False, "runbooks": items}


# --- evaluation --------------------------------------------------------------


@router.post("/evaluation/run")
def evaluation_run(request: Request, window_size: int = Query(default=200, ge=20, le=2000)) -> dict[str, Any]:
    runtime = get_runtime(request)
    llm_ok = None
    if runtime.llm is not None and runtime.settings.llm_requested:
        llm_ok, _ = runtime.llm.available()
    report = run_evaluation(runtime.analyzer, window_size=window_size, llm_available=llm_ok)
    request.app.state.last_evaluation = report
    return report.to_dict()


@router.get("/evaluation")
def evaluation_get(request: Request, run: bool = Query(default=False)) -> Response:
    if run:
        return evaluation_run(request)
    report: EvaluationReport | None = getattr(request.app.state, "last_evaluation", None)
    if report is None:
        raise HTTPException(
            status_code=404, detail="no evaluation run yet; POST /evaluation/run or GET /evaluation?run=true"
        )
    return JSONResponse(report.to_dict())


# --- business endpoints (the workload under observation) ---------------------


@router.get("/api/products")
def api_products(request: Request) -> Response:
    return _business_response(request, "products")


@router.get("/api/cart")
def api_cart(request: Request) -> Response:
    return _business_response(request, "cart")


@router.get("/api/orders")
def api_orders(request: Request) -> Response:
    return _business_response(request, "orders")


@router.post("/api/orders/checkout")
def api_checkout(request: Request) -> Response:
    return _business_response(request, "checkout")


def _business_response(request: Request, operation: str) -> Response:
    """Return normal behaviour, or the deterministic fault response when injected."""
    runtime = get_runtime(request)
    simulator = get_simulator(request)
    fault = runtime.injector.current()
    if fault:
        spec = FAULT_SPECS[fault.scenario]
        body = simulator.make_request_error(fault) if simulator else {"fault": fault.scenario}
        return JSONResponse(body, status_code=spec.http_status)
    return JSONResponse(
        {
            "status": "ok",
            "operation": operation,
            "service": runtime.settings.app_name,
            "items": 3 if operation in {"products", "cart"} else 1,
        }
    )


# --- admin: failure injection ------------------------------------------------


@router.post("/admin/faults", dependencies=[Depends(require_admin)])
def inject_fault(request: Request, payload: FaultRequest) -> dict[str, Any]:
    runtime = get_runtime(request)
    try:
        scenario = normalize_scenario(payload.scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    fault = runtime.injector.activate(scenario, duration_seconds=payload.duration_seconds, injected_by="api")
    duration = payload.duration_seconds or runtime.settings.fault_duration_seconds
    logger.info("fault injected: %s (%.0fs)", scenario, duration)
    return {
        "injected": True,
        "fault": fault.to_dict(),
        "ground_truth": fault.ground_truth,
        "evaluation_scenario": scenario in EVALUATION_SCENARIOS,
    }


@router.get("/admin/faults", dependencies=[Depends(require_admin)])
def fault_state(request: Request) -> dict[str, Any]:
    runtime = get_runtime(request)
    return runtime.injector.snapshot()


@router.post("/admin/faults/clear", dependencies=[Depends(require_admin)])
def clear_fault(request: Request) -> dict[str, Any]:
    runtime = get_runtime(request)
    previous = runtime.injector.clear()
    return {"cleared": True, "previous": previous}


@router.delete("/admin/faults", dependencies=[Depends(require_admin)])
def delete_fault(request: Request) -> dict[str, Any]:
    return clear_fault(request)


# --- config ------------------------------------------------------------------


@router.get("/config")
def public_config(request: Request) -> dict[str, Any]:
    runtime = get_runtime(request)
    # Explicitly safe view; never includes ADMIN_TOKEN or webhook URL.
    return runtime.settings.to_public_dict()
