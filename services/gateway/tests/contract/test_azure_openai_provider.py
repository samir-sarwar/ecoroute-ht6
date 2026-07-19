from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from ecoroute.api.schemas import ChatCompletionRequest
from ecoroute.config import Settings
from ecoroute.db.models import ModelEndpoint
from ecoroute.providers.azure_openai import AzureOpenAIProvider
from ecoroute.providers.base import ProviderError
from ecoroute.providers.fake import FakeProvider
from ecoroute.providers.registry import ProviderRegistry


def endpoint() -> ModelEndpoint:
    return ModelEndpoint(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="azure-canada",
        provider="azure_openai",
        base_url="https://contract.openai.azure.com/openai/v1",
        credential_ref="env:AZURE_CONTRACT_KEY",
        physical_model="gpt-deployment-canada",
        azure_deployment_type="standard",
        region="canada-central",
        grid_zone="CA-ON",
        quality_tier="frontier",
        capabilities=["text", "streaming"],
        context_window_tokens=4096,
        input_usd_per_million_tokens=Decimal(0),
        output_usd_per_million_tokens=Decimal(0),
        fixed_request_kwh=0,
        input_kwh_per_1k_tokens=0,
        output_kwh_per_1k_tokens=0,
        energy_evidence="estimated",
        latency_p50_ms=1,
        latency_p95_ms=10,
        self_hosted=False,
        baseline_concurrency=16,
        concurrency_target=16,
    )


def request() -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(
        {
            "model": "logical-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"client_app": "contract-test"},
            "future_flag": "forward-me",
        }
    )


def adapter(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> AzureOpenAIProvider:
    monkeypatch.setenv("AZURE_CONTRACT_KEY", "azure-secret")
    return AzureOpenAIProvider(
        Settings(
            ECOROUTE_ENV="test",
            ECOROUTE_DEMO_MODE=False,
            ECOROUTE_ALLOWED_CREDENTIAL_ENVS="AZURE_CONTRACT_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )


def test_registry_uses_fixture_only_in_demo_mode() -> None:
    demo = ProviderRegistry(Settings(ECOROUTE_DEMO_MODE=True))
    live = ProviderRegistry(Settings(ECOROUTE_DEMO_MODE=False))

    assert isinstance(demo.for_provider("azure_openai"), FakeProvider)
    assert isinstance(live.for_provider("azure_openai"), AzureOpenAIProvider)


@pytest.mark.asyncio
async def test_chat_uses_azure_v1_endpoint_api_key_and_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(upstream: httpx.Request) -> httpx.Response:
        seen["path"] = upstream.url.path
        seen["api_key"] = upstream.headers.get("api-key")
        seen["authorization"] = upstream.headers.get("authorization")
        seen["body"] = json.loads(upstream.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-azure",
                "object": "chat.completion",
                "model": "gpt-deployment-canada",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    response = await adapter(monkeypatch, handler).chat(endpoint(), request())

    assert response["choices"][0]["message"]["content"] == "Hello"
    assert seen["path"] == "/openai/v1/chat/completions"
    assert seen["api_key"] == "azure-secret"
    assert seen["authorization"] is None
    assert seen["body"]["model"] == "gpt-deployment-canada"
    assert seen["body"]["future_flag"] == "forward-me"
    assert "metadata" not in seen["body"]


@pytest.mark.asyncio
async def test_gpt5_chat_normalizes_unsupported_reasoning_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    target = endpoint()
    target.physical_model = "gpt-5.4-mini"
    chat_request = ChatCompletionRequest.model_validate(
        {
            "model": "logical-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0,
            "top_p": 1,
            "max_tokens": 256,
            "presence_penalty": 0,
            "frequency_penalty": 0,
        }
    )

    def handler(upstream: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(upstream.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-azure-gpt5",
                "object": "chat.completion",
                "model": "gpt-5.4-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    await adapter(monkeypatch, handler).chat(target, chat_request)

    assert seen["body"]["model"] == "gpt-5.4-mini"
    assert seen["body"]["max_completion_tokens"] == 256
    for parameter in AzureOpenAIProvider._GPT5_UNSUPPORTED_PARAMETERS | {"max_tokens"}:
        assert parameter not in seen["body"]


@pytest.mark.asyncio
async def test_non_streaming_chat_drops_stream_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    chat_request = ChatCompletionRequest.model_validate(
        {
            "model": "logical-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    )

    def handler(upstream: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(upstream.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-azure",
                "object": "chat.completion",
                "model": "gpt-deployment-canada",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    await adapter(monkeypatch, handler).chat(endpoint(), chat_request)

    assert seen["body"]["stream"] is False
    assert "stream_options" not in seen["body"]


@pytest.mark.asyncio
async def test_health_is_authenticated_and_reports_deployment_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(upstream: httpx.Request) -> httpx.Response:
        assert upstream.url.path == "/openai/v1/models"
        assert upstream.headers["api-key"] == "azure-secret"
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-deployment-canada", "object": "model"}]},
        )

    result = await adapter(monkeypatch, handler).health(endpoint())

    assert result["status"] == "healthy"
    assert result["deploymentVisible"] is True
    assert result["deploymentType"] == "standard"


@pytest.mark.asyncio
async def test_stream_parses_sse_and_requires_terminal_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = {
        "id": "chatcmpl-azure",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    content = (
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-azure",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
            }
        )
        + "\n\ndata: "
        + json.dumps(terminal)
        + "\n\ndata: [DONE]\n\n"
    )

    def handler(upstream: httpx.Request) -> httpx.Response:
        assert json.loads(upstream.content)["stream"] is True
        return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})

    chunks = [item async for item in adapter(monkeypatch, handler).stream(endpoint(), request())]

    assert chunks[0]["choices"][0]["delta"]["content"] == "Hello"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_azure_errors_do_not_leak_upstream_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(upstream: httpx.Request) -> httpx.Response:
        del upstream
        return httpx.Response(401, json={"error": {"message": "sensitive upstream detail"}})

    with pytest.raises(ProviderError) as captured:
        await adapter(monkeypatch, handler).chat(endpoint(), request())

    assert captured.value.code == "upstream_auth_error"
    assert "sensitive" not in str(captured.value)
