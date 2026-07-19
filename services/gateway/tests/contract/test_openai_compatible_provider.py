from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
import uvicorn
from ecoroute.api.schemas import ChatCompletionRequest
from ecoroute.config import Settings
from ecoroute.db.models import ModelEndpoint
from ecoroute.providers.base import ProviderError
from ecoroute.providers.openai_compatible import OpenAICompatibleProvider
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

fake_upstream = FastAPI()


def _completion(payload: dict[str, Any], *, usage: bool = True) -> dict[str, Any]:
    model = str(payload["model"])
    response: dict[str, Any] = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Contract response"},
                "finish_reason": "stop",
            }
        ],
    }
    if payload.get("response_format"):
        response["choices"][0]["message"]["content"] = json.dumps({"status": "ok"})
    if payload.get("future_flag") == "forwarded":
        response["choices"][0]["message"]["content"] = "Unknown field forwarded"
    if payload.get("tools"):
        response["choices"][0] = {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_contract",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"id":"one"}'},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    if usage:
        response["usage"] = {
            "prompt_tokens": 5,
            "completion_tokens": 2,
            "total_tokens": 7,
        }
    return response


@fake_upstream.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "contract-normal", "object": "model"}]}


@fake_upstream.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True}


@fake_upstream.get("/adapters/{adapter_id}")
async def adapter_status(adapter_id: str, request: Request) -> Any:
    if request.headers.get("Authorization") != "Bearer contract-secret":
        return JSONResponse({"detail": "invalid key"}, status_code=401)
    return {"adapter_id": adapter_id, "status": "ready"}


@fake_upstream.post("/v1/chat/completions")
async def completions(request: Request) -> Any:
    payload = await request.json()
    model = str(payload["model"])
    if model.endswith("delayed"):
        await asyncio.sleep(0.05)
    if model.endswith("rate-limit"):
        return JSONResponse(
            {"error": {"message": "limited", "type": "rate_limit_error"}},
            status_code=429,
            headers={"Retry-After": "1"},
        )
    if model.endswith("server-error"):
        return JSONResponse({"error": {"message": "failed"}}, status_code=500)
    if model.endswith("malformed"):
        return PlainTextResponse('{"not":"a completion"', media_type="application/json")
    if model.endswith("abrupt") and not payload.get("stream"):

        async def disconnect() -> AsyncIterator[bytes]:
            yield b'{"id":"partial"'
            raise RuntimeError("contract disconnect")

        return StreamingResponse(disconnect(), media_type="application/json")
    if payload.get("stream"):

        async def events() -> AsyncIterator[str]:
            base = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
            }
            for content in ("Contract ", "stream"):
                yield (
                    "data: "
                    + json.dumps(
                        {
                            **base,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": content},
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                    + "\n\n"
                )
                if model.endswith("abrupt"):
                    raise RuntimeError("contract disconnect")
            yield (
                "data: "
                + json.dumps(
                    {
                        **base,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                )
                + "\n\n"
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")
    return _completion(payload, usage=not model.endswith("missing-usage"))


@pytest_asyncio.fixture
async def upstream_url(unused_tcp_port: int) -> AsyncIterator[str]:
    server = uvicorn.Server(
        uvicorn.Config(
            fake_upstream,
            host="127.0.0.1",
            port=unused_tcp_port,
            log_level="error",
        )
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{unused_tcp_port}/v1"
    finally:
        server.should_exit = True
        await task


def endpoint(base_url: str, model: str = "contract-normal") -> ModelEndpoint:
    return ModelEndpoint(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name=model,
        provider="openai_compatible",
        base_url=base_url,
        credential_ref="env:CONTRACT_API_KEY",
        physical_model=model,
        region="test",
        grid_zone="test",
        quality_tier="frontier",
        capabilities=["text", "json_schema", "tools", "streaming"],
        context_window_tokens=4096,
        input_usd_per_million_tokens=Decimal(0),
        output_usd_per_million_tokens=Decimal(0),
        fixed_request_kwh=0,
        input_kwh_per_1k_tokens=0,
        output_kwh_per_1k_tokens=0,
        energy_evidence="simulated",
        latency_p50_ms=1,
        latency_p95_ms=10,
        self_hosted=False,
        baseline_concurrency=16,
        concurrency_target=16,
    )


def adapter(monkeypatch: pytest.MonkeyPatch) -> OpenAICompatibleProvider:
    monkeypatch.setenv("CONTRACT_API_KEY", "contract-secret")
    return OpenAICompatibleProvider(
        Settings(
            ECOROUTE_ALLOWED_CREDENTIAL_ENVS="CONTRACT_API_KEY",
            ECOROUTE_PROVIDER_TIMEOUT_SECONDS=2,
            ECOROUTE_STREAM_TIMEOUT_SECONDS=2,
        )
    )


@pytest.mark.asyncio
async def test_freesolo_health_uses_service_and_exact_model_completion(
    upstream_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = adapter(monkeypatch)
    model_endpoint = endpoint(upstream_url, "flash-support-contract")
    model_endpoint.provider = "freesolo"

    result = await provider.health(model_endpoint)

    assert result["status"] == "healthy"
    assert result["provider"] == "freesolo"
    assert result["providerModel"] == "flash-support-contract"


@pytest.mark.asyncio
async def test_normal_delayed_structured_tool_and_missing_usage_contracts(
    upstream_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = adapter(monkeypatch)
    ordinary = ChatCompletionRequest(
        model="logical", messages=[{"role": "user", "content": "Hello"}]
    )
    for model in ("contract-normal", "contract-delayed", "contract-missing-usage"):
        response = await provider.chat(endpoint(upstream_url, model), ordinary)
        assert response["choices"][0]["message"]["role"] == "assistant"

    future = ChatCompletionRequest.model_validate(
        {
            "model": "logical",
            "messages": [{"role": "user", "content": "Future"}],
            "future_flag": "forwarded",
        }
    )
    forwarded = await provider.chat(endpoint(upstream_url), future)
    assert forwarded["choices"][0]["message"]["content"] == "Unknown field forwarded"

    structured = ordinary.model_copy(update={"response_format": {"type": "json_object"}})
    structured_response = await provider.chat(endpoint(upstream_url), structured)
    assert json.loads(structured_response["choices"][0]["message"]["content"]) == {"status": "ok"}

    tools = ordinary.model_copy(
        update={
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Lookup",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        }
    )
    tool_response = await provider.chat(endpoint(upstream_url), tools)
    assert tool_response["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "lookup"


@pytest.mark.asyncio
async def test_chunked_stream_contract(upstream_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = adapter(monkeypatch)
    request = ChatCompletionRequest(
        model="logical",
        messages=[{"role": "user", "content": "Stream"}],
        stream=True,
    )
    chunks = [chunk async for chunk in provider.stream(endpoint(upstream_url), request)]
    content = "".join(
        str(choice.get("delta", {}).get("content") or "")
        for chunk in chunks
        for choice in chunk.get("choices", [])
    )
    assert content == "Contract stream"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_abrupt_disconnect_is_normalized(
    upstream_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = adapter(monkeypatch)
    request = ChatCompletionRequest(
        model="logical",
        messages=[{"role": "user", "content": "Disconnect"}],
    )
    with pytest.raises(ProviderError) as captured:
        await provider.chat(endpoint(upstream_url, "contract-abrupt"), request)
    assert captured.value.code in {"upstream_transport_error", "upstream_error"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "expected_code"),
    [
        ("contract-rate-limit", "rate_limit_error"),
        ("contract-server-error", "upstream_error"),
        ("contract-malformed", "upstream_error"),
    ],
)
async def test_upstream_failures_are_normalized(
    upstream_url: str,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    expected_code: str,
) -> None:
    provider = adapter(monkeypatch)
    request = ChatCompletionRequest(
        model="logical", messages=[{"role": "user", "content": "Failure"}]
    )
    with pytest.raises(ProviderError) as captured:
        await provider.chat(endpoint(upstream_url, model), request)
    assert captured.value.code == expected_code
