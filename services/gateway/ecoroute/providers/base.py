from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from ecoroute.api.schemas import ChatCompletionRequest
from ecoroute.db.models import ModelEndpoint


class ProviderError(RuntimeError):
    def __init__(self, message: str, code: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ProviderAdapter(Protocol):
    async def health(self, endpoint: ModelEndpoint) -> dict[str, Any]: ...
    async def chat(
        self, endpoint: ModelEndpoint, request: ChatCompletionRequest
    ) -> dict[str, Any]: ...
    def stream(
        self, endpoint: ModelEndpoint, request: ChatCompletionRequest
    ) -> AsyncIterator[dict[str, Any]]: ...
    async def count_tokens(
        self, endpoint: ModelEndpoint, request: ChatCompletionRequest
    ) -> int: ...
