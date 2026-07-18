from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class EcoRouteError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        code: str = "invalid_request",
        error_type: str = "invalid_request_error",
        param: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.error_type = error_type
        self.param = param
        self.details = details or {}


def openai_error(error: EcoRouteError, request_id: str | None) -> JSONResponse:
    response = JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "message": str(error),
                "type": error.error_type,
                "param": error.param,
                "code": error.code,
            }
        },
    )
    if request_id:
        response.headers["X-Request-Id"] = request_id
    return response


async def ecoroute_error_handler(request: Request, exc: EcoRouteError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    if request.url.path.startswith("/v1/"):
        return openai_error(exc, request_id)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": exc.details,
                "requestId": request_id,
            }
        },
        headers={"X-Request-Id": request_id} if request_id else None,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    safe_errors = [
        {key: value for key, value in item.items() if key != "input"} for item in exc.errors()
    ]
    error = EcoRouteError(
        "Request validation failed",
        status_code=400,
        code="invalid_body",
        details={"errors": safe_errors},
    )
    return await ecoroute_error_handler(request, error)
