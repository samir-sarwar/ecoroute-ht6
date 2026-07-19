from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from urllib.parse import urlparse

import httpx
import litellm
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from litellm.utils import token_counter

from ecoroute.api.schemas import ChatCompletionRequest
from ecoroute.config import Settings
from ecoroute.db.models import ModelEndpoint
from ecoroute.providers.base import ProviderError

litellm.suppress_debug_info = True

_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.google",
    "instance-data",
}


async def validate_network_target(endpoint: ModelEndpoint, settings: Settings) -> None:
    """Resolve production endpoint hosts before use so DNS names cannot mask private targets."""
    parsed = urlparse(endpoint.base_url)
    host = (parsed.hostname or "").casefold()
    allowed = {
        item.strip().casefold()
        for item in settings.allowed_endpoint_hosts.split(",")
        if item.strip()
    }
    if host in allowed:
        return
    if host in _METADATA_HOSTS:
        raise ProviderError(
            "Endpoint URL targets a blocked metadata service", "invalid_endpoint_url"
        )
    if settings.environment in {"development", "test"}:
        return
    try:
        direct = ipaddress.ip_address(host)
        addresses = [direct]
    except ValueError:
        try:
            async with asyncio.timeout(1):
                records = await asyncio.get_running_loop().getaddrinfo(
                    host,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            addresses = list({ipaddress.ip_address(record[4][0]) for record in records})
        except (OSError, TimeoutError, ValueError) as exc:
            raise ProviderError(
                "Endpoint hostname could not be safely resolved", "upstream_transport_error", 502
            ) from exc
    if any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
        for address in addresses
    ):
        raise ProviderError(
            "Private, loopback, and link-local endpoint targets require an explicit allowlist",
            "invalid_endpoint_url",
        )


def resolve_credential(reference: str | None, settings: Settings) -> str | None:
    if reference is None:
        return None
    if not reference.startswith("env:"):
        raise ProviderError("Only env: credential references are allowed", "invalid_credential_ref")
    variable = reference[4:]
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", variable):
        raise ProviderError("Credential reference is not allowlisted", "invalid_credential_ref")
    allowlist = {
        item.strip() for item in settings.allowed_credential_envs.split(",") if item.strip()
    }
    if variable not in allowlist:
        raise ProviderError(
            "Credential environment variable is not allowlisted", "invalid_credential_ref"
        )
    return os.environ.get(variable)


def _health_url(endpoint: ModelEndpoint) -> str:
    """Readiness probe URL, since not every OpenAI-compatible server lists models."""
    base = endpoint.base_url.rstrip("/")
    if endpoint.provider == "freesolo":
        # FreeSOLO serves chat completions under /v1 but implements no /v1/models
        # route; its readiness endpoint sits at the service root.
        return re.sub(r"/v1$", "", base) + "/healthz"
    return base + "/models"


def _litellm_model(endpoint: ModelEndpoint) -> str:
    prefixes = {
        "gemini": "gemini",
        "openai": "openai",
        "ollama": "ollama",
        "vllm": "openai",
        "openai_compatible": "openai",
        "freesolo": "openai",
    }
    prefix = prefixes.get(endpoint.provider, "openai")
    if endpoint.physical_model.startswith(prefix + "/"):
        return endpoint.physical_model
    return f"{prefix}/{endpoint.physical_model}"


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return cast(dict[str, Any], value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return value
    raise ProviderError("Upstream returned an invalid response", "upstream_invalid_response")


class EndpointLimiter:
    def __init__(self) -> None:
        self._active = 0
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def slot(self, target: int) -> AsyncIterator[None]:
        async with self._condition:
            await self._condition.wait_for(lambda: self._active < max(1, target))
            self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()


class OpenAICompatibleProvider:
    """LiteLLM-backed adapter for every non-fixture provider.

    EcoRoute owns routing and retry policy; LiteLLM is used only to normalize individual
    provider calls and response/error shapes.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._limiters: dict[str, EndpointLimiter] = {}

    def _limiter(self, endpoint: ModelEndpoint) -> EndpointLimiter:
        return self._limiters.setdefault(str(endpoint.id), EndpointLimiter())

    def _payload(
        self,
        endpoint: ModelEndpoint,
        request: ChatCompletionRequest,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        token = resolve_credential(endpoint.credential_ref, self.settings)
        if endpoint.credential_ref and not token:
            raise ProviderError(
                "Endpoint credential is not configured",
                "upstream_auth_missing",
                502,
            )
        extra_names = set((request.model_extra or {}).keys())
        payload = request.model_dump(exclude_none=True, exclude=extra_names)
        payload.pop("metadata", None)
        if request.model_extra:
            payload["extra_body"] = request.model_extra
        payload["model"] = _litellm_model(endpoint)
        payload["stream"] = stream
        payload["api_key"] = token or "not-required"
        payload["timeout"] = (
            self.settings.stream_timeout_seconds
            if stream
            else self.settings.provider_timeout_seconds
        )
        payload["num_retries"] = 0
        payload["max_retries"] = 0
        # Native Gemini uses its provider integration; local and generic services expose an
        # OpenAI-compatible base URL and are routed through LiteLLM's OpenAI normalizer.
        if (
            endpoint.provider != "gemini"
            or "generativelanguage.googleapis.com" not in endpoint.base_url
        ):
            payload["api_base"] = endpoint.base_url.rstrip("/")
        return payload

    @staticmethod
    def _provider_error(exc: Exception) -> ProviderError:
        if isinstance(exc, AuthenticationError):
            return ProviderError("Upstream authentication failed", "upstream_auth_error", 502)
        if isinstance(exc, RateLimitError):
            return ProviderError("Upstream rate limit reached", "rate_limit_error", 429)
        if isinstance(exc, Timeout):
            return ProviderError("Upstream timed out", "upstream_timeout", 504)
        if isinstance(exc, (APIConnectionError, ServiceUnavailableError)):
            return ProviderError("Upstream transport failed", "upstream_transport_error", 502)
        if isinstance(exc, BadRequestError):
            return ProviderError("Upstream rejected the request", "upstream_bad_request", 502)
        return ProviderError("Upstream request failed", "upstream_error", 502)

    async def health(self, endpoint: ModelEndpoint) -> dict[str, Any]:
        started = asyncio.get_running_loop().time()
        token = resolve_credential(endpoint.credential_ref, self.settings)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = _health_url(endpoint)
        params = {"key": token} if endpoint.provider == "gemini" and token else None
        try:
            await validate_network_target(endpoint, self.settings)
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
            return {
                "status": "healthy",
                "provider": endpoint.provider,
                "providerModel": endpoint.physical_model,
                "latencyMs": int((asyncio.get_running_loop().time() - started) * 1000),
            }
        except (httpx.HTTPError, ProviderError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            detail = f" (HTTP {status})" if status is not None else ""
            return {
                "status": "unhealthy",
                "provider": endpoint.provider,
                "error": type(exc).__name__,
                "message": f"Health probe failed for {url}{detail}",
            }

    async def chat(self, endpoint: ModelEndpoint, request: ChatCompletionRequest) -> dict[str, Any]:
        try:
            await validate_network_target(endpoint, self.settings)
            async with self._limiter(endpoint).slot(endpoint.concurrency_target):
                response = await litellm.acompletion(
                    **self._payload(endpoint, request, stream=False)
                )
            return _as_dict(response)
        except ProviderError:
            raise
        except Exception as exc:
            raise self._provider_error(exc) from exc

    async def stream(
        self, endpoint: ModelEndpoint, request: ChatCompletionRequest
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            await validate_network_target(endpoint, self.settings)
            async with self._limiter(endpoint).slot(endpoint.concurrency_target):
                response = await litellm.acompletion(
                    **self._payload(endpoint, request, stream=True)
                )
                saw_chunk = False
                saw_terminal_choice = False
                async for chunk in response:
                    value = _as_dict(chunk)
                    saw_chunk = True
                    saw_terminal_choice = saw_terminal_choice or any(
                        choice.get("finish_reason") is not None
                        for choice in value.get("choices", [])
                        if isinstance(choice, dict)
                    )
                    yield value
                if not saw_chunk or not saw_terminal_choice:
                    raise ProviderError(
                        "Upstream stream ended before a terminal chunk",
                        "upstream_transport_error",
                        502,
                    )
        except ProviderError:
            raise
        except Exception as exc:
            raise self._provider_error(exc) from exc

    async def count_tokens(self, endpoint: ModelEndpoint, request: ChatCompletionRequest) -> int:
        try:
            return int(
                token_counter(
                    model=_litellm_model(endpoint),
                    messages=[
                        message.model_dump(exclude_none=True) for message in request.messages
                    ],
                )
            )
        except Exception:
            return max(1, sum(len(message.text()) for message in request.messages) // 4)
