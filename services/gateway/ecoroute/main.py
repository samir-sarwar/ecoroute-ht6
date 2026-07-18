from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select, text

from ecoroute.api.control import agent_router
from ecoroute.api.control import router as control_router
from ecoroute.api.errors import EcoRouteError, ecoroute_error_handler, validation_error_handler
from ecoroute.api.gateway import router as gateway_router
from ecoroute.config import get_settings
from ecoroute.db.base import uuid7
from ecoroute.db.models import LogicalModel
from ecoroute.db.session import SessionLocal, redis_client

settings = get_settings()
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger(service="gateway")
app = FastAPI(
    title="EcoRoute AI Gateway",
    version="0.1.0",
    description="OpenAI-compatible carbon, cost, cache, and quality-aware routing gateway.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_url, "http://localhost:3000", "http://localhost:3001"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"],
    expose_headers=[
        "X-Request-Id",
        "X-EcoRoute-Request-Id",
        "X-EcoRoute-Route",
        "X-EcoRoute-Endpoint-Id",
        "X-EcoRoute-Cache",
        "X-EcoRoute-Evidence",
        "X-EcoRoute-Fallback",
        "X-EcoRoute-Carbon-Accounting",
        "X-EcoRoute-Grid-Attribution",
    ],
)
app.add_exception_handler(EcoRouteError, ecoroute_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.include_router(gateway_router)
app.include_router(control_router)
app.include_router(agent_router)


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = str(uuid7())
    request.state.request_id = request_id
    started = time.monotonic()
    content_length = request.headers.get("content-length")
    try:
        body_too_large = bool(
            content_length and int(content_length) > settings.max_request_body_bytes
        )
    except ValueError:
        body_too_large = True
    if body_too_large:
        return JSONResponse(
            {
                "error": {
                    "message": "Request body is too large",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "request_too_large",
                }
            },
            status_code=413,
            headers={"X-Request-Id": request_id},
        )
    if request.method in {"POST", "PUT", "PATCH"}:
        body = await request.body()
        if len(body) > settings.max_request_body_bytes:
            return JSONResponse(
                {
                    "error": {
                        "message": "Request body is too large",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "request_too_large",
                    }
                },
                status_code=413,
                headers={"X-Request-Id": request_id},
            )
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error(
            "gateway.request",
            outcome="failed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=type(exc).__name__,
        )
        raise
    response.headers.setdefault("X-Request-Id", request_id)
    if settings.environment == "development":
        response.headers["Server-Timing"] = f"app;dur={(time.monotonic() - started) * 1000:.2f}"
    logger.info(
        "gateway.request",
        outcome="completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return response


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readiness() -> JSONResponse:
    dependencies: dict[str, str] = {}
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
            current_revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            alembic_config = Config("services/gateway/alembic.ini")
            expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()
            if current_revision != expected_revision:
                raise RuntimeError(
                    f"database migration is {current_revision or 'none'}, expected {expected_revision}"
                )
            logical = await session.scalar(
                select(LogicalModel).where(
                    LogicalModel.enabled.is_(True),
                    LogicalModel.required_fallback_endpoint_id.is_not(None),
                )
            )
            if logical is None:
                raise RuntimeError("no configured logical model fallback")
        dependencies["postgres"] = "ready"
    except Exception as exc:
        dependencies["postgres"] = f"unavailable:{type(exc).__name__}"
    try:
        if not await redis_client.ping():
            raise RuntimeError("ping failed")
        dependencies["redis"] = "ready"
    except Exception as exc:
        dependencies["redis"] = f"unavailable:{type(exc).__name__}"
    try:
        carbon_keys = [key async for key in redis_client.scan_iter("ecoroute:carbon:*", count=1)]
        dependencies["carbon"] = "ready" if carbon_keys else "degraded:not_cached"
    except Exception:
        dependencies["carbon"] = "degraded:unknown"
    ready = dependencies.get("postgres") == "ready" and dependencies.get("redis") == "ready"
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "dependencies": dependencies},
        status_code=200 if ready else 503,
    )


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
