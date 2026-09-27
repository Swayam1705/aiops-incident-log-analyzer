"""HTTP middleware: request metrics, request IDs, and security headers.

* Records real request count / error count / latency per route (drives Prometheus).
* Assigns a request_id and attaches it to the log context for correlation.
* Adds conservative security headers. The UI is same-origin and executes no shell,
  kubectl or remediation; these headers are defense-in-depth, not the primary control.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.metrics import MetricsRegistry

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)

# Route templates we track; anything else is bucketed as "other" to avoid
# high-cardinality label explosion from arbitrary paths.
_TRACKED_PATHS = {
    "/",
    "/health",
    "/health/live",
    "/health/ready",
    "/metrics",
    "/analyze",
    "/incidents",
    "/logs",
    "/evaluation",
    "/evaluation/run",
    "/runbooks",
    "/admin/faults",
    "/admin/faults/clear",
    "/ui",
    "/ui/incidents",
    "/ui/evaluation",
    "/ui/health",
}


def _bucket_path(path: str) -> str:
    if path in _TRACKED_PATHS:
        return path
    if path.startswith("/api/"):
        return path  # the business endpoints under observation (bounded, fixed set)
    if path.startswith("/incidents/"):
        return "/incidents/{id}"
    if path.startswith("/ui/incidents/"):
        return "/ui/incidents/{id}"
    if path.startswith("/admin/"):
        return "/admin/*"
    if path.startswith("/static/"):
        return "/static/*"
    return "other"


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Callable, metrics: MetricsRegistry) -> None:
        super().__init__(app)
        self.metrics = metrics

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:12]}"
        token = current_request_id.set(request_id)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            current_request_id.reset(token)
            bucket = _bucket_path(request.url.path)
            # Metrics endpoint and static assets are not part of the app's own SLO
            # signal; skip them so request/error rates reflect real workload.
            if bucket not in {"/metrics", "/static/*"}:
                outcome = "error" if status_code >= 500 else "ok"
                self.metrics.request_total.inc(
                    method=request.method, path=bucket, status=str(status_code), outcome=outcome
                )
                self.metrics.request_latency.observe(
                    duration, method=request.method, path=bucket, outcome=outcome
                )


def add_security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'",
    )
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=(), interest-cohort=()"
    )
    return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        return add_security_headers(response)
