"""Privacy-safe HTTP telemetry for the API process."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info, generate_latest
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = b"x-request-id"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    """Emit a small allowlisted JSON record without exception or request payloads."""

    allowed_extra = (
        "request_id",
        "http_method",
        "http_route",
        "http_status",
        "duration_ms",
        "exception_type",
        "environment",
        "revision",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in self.allowed_extra:
            value = getattr(record, key, None)
            if isinstance(value, (str, int, float, bool)):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(environment: str, revision: str) -> None:
    """Use one JSON stream and disable Uvicorn's duplicate access log."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error"):
        child = logging.getLogger(name)
        child.handlers = []
        child.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("app.startup").info(
        "application_started",
        extra={"environment": environment, "revision": revision},
    )


class HttpMetrics:
    """Per-process Prometheus registry with bounded, non-tenant labels."""

    def __init__(self, *, environment: str, revision: str) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.requests = Counter(
            "photolamp_http_requests_total",
            "Completed API HTTP requests.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self.duration = Histogram(
            "photolamp_http_request_duration_seconds",
            "API HTTP request duration in seconds.",
            ("method", "route"),
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
            registry=self.registry,
        )
        self.in_progress = Gauge(
            "photolamp_http_requests_in_progress",
            "API HTTP requests currently executing.",
            registry=self.registry,
        )
        self.exceptions = Counter(
            "photolamp_http_exceptions_total",
            "Unhandled API HTTP exceptions converted to safe responses.",
            ("method", "route", "exception_type"),
            registry=self.registry,
        )
        build = Info(
            "photolamp_build",
            "Running API build information.",
            registry=self.registry,
        )
        build.info({"environment": environment, "revision": revision})

    def render(self) -> bytes:
        return generate_latest(self.registry)


def request_id_from(value: str | None) -> str:
    if value and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid4().hex


def route_label(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class ObservabilityMiddleware:
    """Add request IDs, metrics and one safe completion log per HTTP request."""

    def __init__(self, app: ASGIApp, metrics: HttpMetrics) -> None:
        self.app = app
        self.metrics = metrics
        self.logger = logging.getLogger("app.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        supplied_id = headers.get(REQUEST_ID_HEADER, b"").decode("ascii", errors="ignore")
        request_id = request_id_from(supplied_id)
        token = request_id_context.set(request_id)
        method = str(scope.get("method", "UNKNOWN")).upper()
        status = 500
        response_started = False
        started = perf_counter()
        self.metrics.in_progress.inc()

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started, status
            if message["type"] == "http.response.start":
                response_started = True
                status = int(message["status"])
                response_headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != REQUEST_ID_HEADER
                ]
                response_headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        exception_type: str | None = None
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as error:
            exception_type = type(error).__name__
            if response_started:
                raise
            response = JSONResponse({"detail": "Internal server error"}, status_code=500)
            await response(scope, receive, send_with_request_id)
        finally:
            route = route_label(scope)
            duration = perf_counter() - started
            self.metrics.in_progress.dec()
            self.metrics.requests.labels(method=method, route=route, status=str(status)).inc()
            self.metrics.duration.labels(method=method, route=route).observe(duration)
            if exception_type:
                self.metrics.exceptions.labels(
                    method=method, route=route, exception_type=exception_type
                ).inc()
            extra: dict[str, Any] = {
                "request_id": request_id,
                "http_method": method,
                "http_route": route,
                "http_status": status,
                "duration_ms": round(duration * 1000, 3),
            }
            if exception_type:
                extra["exception_type"] = exception_type
            self.logger.log(
                logging.ERROR if status >= 500 else logging.INFO,
                "http_request_completed",
                extra=extra,
            )
            request_id_context.reset(token)


def metrics_response(metrics: HttpMetrics) -> tuple[bytes, str]:
    return metrics.render(), "text/plain; version=0.0.4; charset=utf-8"
