from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx

from ecoroute.api.schemas import ChatCompletionRequest
from ecoroute.config import Settings
from ecoroute.db.models import ModelEndpoint
from ecoroute.providers.base import ProviderError
from ecoroute.providers.openai_compatible import (
    EndpointLimiter,
    resolve_credential,
    validate_network_target,
)


class AzureOpenAIProvider:
    """Azure OpenAI v1 data-plane adapter for explicitly regional deployments."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._limiters: dict[str, EndpointLimiter] = {}

    def _limiter(self, endpoint: ModelEndpoint) -> EndpointLimiter:
        return self._limiters.setdefault(str(endpoint.id), EndpointLimiter())

    def _credential(self, endpoint: ModelEndpoint) -> str:
        token = resolve_credential(endpoint.credential_ref, self.settings)
        if not token:
            raise ProviderError(
                "Azure OpenAI credential is not configured",
                "upstream_auth_missing",
                502,
            )
        return token

    def _headers(self, endpoint: ModelEndpoint) -> dict[str, str]:
        return {
            "api-key": self._credential(endpoint),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _payload(
        endpoint: ModelEndpoint,
        request: ChatCompletionRequest,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload = request.model_dump(exclude_none=True)
        # EcoRoute metadata is gateway-local and must never be forwarded upstream.
        payload.pop("metadata", None)
        payload["model"] = endpoint.physical_model
        payload["stream"] = stream
        return payload

    @staticmethod
    def _status_error(status_code: int) -> ProviderError:
        if status_code in {401, 403}:
            return ProviderError("Azure OpenAI authentication failed", "upstream_auth_error", 502)
        if status_code == 429:
            return ProviderError("Azure OpenAI rate limit reached", "rate_limit_error", 429)
        if status_code in {408, 504}:
            return ProviderError("Azure OpenAI timed out", "upstream_timeout", 504)
        if status_code in {400, 404, 409, 422}:
            return ProviderError(
                "Azure OpenAI rejected the request or deployment name",
                "upstream_bad_request",
                502,
            )
        return ProviderError("Azure OpenAI request failed", "upstream_error", 502)

    @staticmethod
    def _transport_error(exc: Exception) -> ProviderError:
        if isinstance(exc, httpx.TimeoutException):
            return ProviderError("Azure OpenAI timed out", "upstream_timeout", 504)
        if isinstance(exc, httpx.RequestError):
            return ProviderError("Azure OpenAI transport failed", "upstream_transport_error", 502)
        return ProviderError("Azure OpenAI returned an invalid response", "upstream_error", 502)

    def _client(self, timeout_seconds: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout_seconds, transport=self._transport)

    async def health(self, endpoint: ModelEndpoint) -> dict[str, Any]:
        started = asyncio.get_running_loop().time()
        try:
            await validate_network_target(endpoint, self.settings)
            async with self._client(3) as client:
                response = await client.get(
                    endpoint.base_url.rstrip("/") + "/models",
                    headers=self._headers(endpoint),
                )
            if not response.is_success:
                raise self._status_error(response.status_code)
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise ProviderError(
                    "Azure OpenAI returned an invalid model list", "upstream_invalid_response"
                )
            model_ids = {
                str(item.get("id"))
                for item in body["data"]
                if isinstance(item, dict) and item.get("id")
            }
            return {
                "status": "healthy",
                "provider": endpoint.provider,
                "providerModel": endpoint.physical_model,
                "deploymentType": endpoint.azure_deployment_type,
                "deploymentVisible": endpoint.physical_model in model_ids,
                "latencyMs": int((asyncio.get_running_loop().time() - started) * 1000),
            }
        except ProviderError as exc:
            return {
                "status": "unhealthy",
                "provider": endpoint.provider,
                "error": exc.code,
                "message": str(exc),
            }
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            error = self._transport_error(exc)
            return {
                "status": "unhealthy",
                "provider": endpoint.provider,
                "error": error.code,
                "message": str(error),
            }

    async def chat(self, endpoint: ModelEndpoint, request: ChatCompletionRequest) -> dict[str, Any]:
        try:
            await validate_network_target(endpoint, self.settings)
            async with self._limiter(endpoint).slot(endpoint.concurrency_target):
                async with self._client(self.settings.provider_timeout_seconds) as client:
                    response = await client.post(
                        endpoint.base_url.rstrip("/") + "/chat/completions",
                        headers=self._headers(endpoint),
                        json=self._payload(endpoint, request, stream=False),
                    )
            if not response.is_success:
                raise self._status_error(response.status_code)
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("choices"), list):
                raise ProviderError(
                    "Azure OpenAI returned an invalid completion", "upstream_invalid_response"
                )
            return cast(dict[str, Any], body)
        except ProviderError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise self._transport_error(exc) from exc

    async def stream(
        self, endpoint: ModelEndpoint, request: ChatCompletionRequest
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            await validate_network_target(endpoint, self.settings)
            async with self._limiter(endpoint).slot(endpoint.concurrency_target):
                async with self._client(self.settings.stream_timeout_seconds) as client:
                    async with client.stream(
                        "POST",
                        endpoint.base_url.rstrip("/") + "/chat/completions",
                        headers=self._headers(endpoint),
                        json=self._payload(endpoint, request, stream=True),
                    ) as response:
                        if not response.is_success:
                            raise self._status_error(response.status_code)
                        saw_chunk = False
                        saw_terminal_choice = False
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            value = json.loads(data)
                            if not isinstance(value, dict):
                                raise ProviderError(
                                    "Azure OpenAI returned an invalid stream chunk",
                                    "upstream_invalid_response",
                                )
                            saw_chunk = True
                            saw_terminal_choice = saw_terminal_choice or any(
                                choice.get("finish_reason") is not None
                                for choice in value.get("choices", [])
                                if isinstance(choice, dict)
                            )
                            yield cast(dict[str, Any], value)
                        if not saw_chunk or not saw_terminal_choice:
                            raise ProviderError(
                                "Azure OpenAI stream ended before a terminal chunk",
                                "upstream_transport_error",
                                502,
                            )
        except ProviderError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise self._transport_error(exc) from exc

    async def count_tokens(self, endpoint: ModelEndpoint, request: ChatCompletionRequest) -> int:
        del endpoint
        return max(1, sum(len(message.text()) for message in request.messages) // 4)
