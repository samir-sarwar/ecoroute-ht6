from __future__ import annotations

import hmac

from fastapi import Header

from ecoroute.api.errors import EcoRouteError
from ecoroute.config import get_settings


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return ""
    return authorization[7:]


async def require_gateway_key(authorization: str | None = Header(None)) -> None:
    expected = get_settings().gateway_key
    if not hmac.compare_digest(_bearer_token(authorization), expected):
        raise EcoRouteError(
            "Incorrect API key provided",
            status_code=401,
            code="invalid_api_key",
            error_type="authentication_error",
        )


async def require_agent_token(authorization: str | None = Header(None)) -> None:
    expected = get_settings().agent_token
    if not hmac.compare_digest(_bearer_token(authorization), expected):
        raise EcoRouteError(
            "Incorrect node-agent token",
            status_code=401,
            code="invalid_agent_token",
            error_type="authentication_error",
        )
