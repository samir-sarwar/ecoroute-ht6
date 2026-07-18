from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ecoroute.api.schemas import ChatCompletionRequest
from ecoroute.config import Settings
from ecoroute.db.models import ModelEndpoint


def _support_answer(text: str) -> tuple[str, list[str]]:
    lowered = text.lower()
    if "final sale" in lowered:
        return (
            "Final-sale items cannot be returned unless they are defective.",
            ["final-sale"],
        )
    if "eight business" in lowered or "not moved" in lowered:
        return (
            "Please contact support. A shipment with no carrier movement for more than seven business days should be escalated.",
            ["shipping-delay"],
        )
    if "exchange" in lowered:
        return "Exchanges are available when the requested replacement is in stock.", [
            "exchange-stock"
        ]
    if "shipping" in lowered or "delivery" in lowered:
        return "Standard shipping normally takes 3–5 business days.", ["shipping-standard"]
    if "refund" in lowered:
        return "An approved refund may take 5–10 business days to appear.", ["refund-timing"]
    return (
        "Unused items may be returned within 30 days. Final-sale items are excluded unless defective.",
        ["returns-30-day"],
    )


class FakeProvider:
    def __init__(self, settings: Settings) -> None:
        self.delay = settings.fake_provider_delay_ms / 1000
        self.invocations = 0

    async def health(self, endpoint: ModelEndpoint) -> dict[str, Any]:
        return {"status": "healthy", "provider": "fake", "model": endpoint.physical_model}

    async def chat(self, endpoint: ModelEndpoint, request: ChatCompletionRequest) -> dict[str, Any]:
        self.invocations += 1
        await asyncio.sleep(self.delay)
        text = "\n".join(message.text() for message in request.messages if message.role == "user")
        answer, policy_ids = _support_answer(text)
        tool_calls: list[dict[str, Any]] | None = None
        if request.tools:
            function = request.tools[0].get("function", {})
            tool_calls = [
                {
                    "id": "call_fake_lookup",
                    "type": "function",
                    "function": {
                        "name": str(function.get("name", "lookup")),
                        "arguments": json.dumps({"query": text}, separators=(",", ":")),
                    },
                }
            ]
            content = ""
        elif request.response_format and request.response_format.get("type") in {
            "json_object",
            "json_schema",
        }:
            content = json.dumps(
                {"answer": answer, "policy_ids": policy_ids}, separators=(",", ":")
            )
        elif endpoint.quality_tier == "specialized":
            content = json.dumps(
                {
                    "answer": answer,
                    "confidence": 0.96,
                    "policy_ids": policy_ids,
                    "needs_human": False,
                },
                separators=(",", ":"),
            )
        elif "law" in text.lower() or "sue" in text.lower():
            content = (
                "I can summarize the fictional store policy, but I cannot determine legal rights or lawsuit risk. "
                "Consult a qualified Ontario legal professional for advice."
            )
        else:
            content = answer
        prompt_tokens = max(1, len(text) // 4)
        output_tokens = max(1, len(content) // 4)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content if tool_calls is None else None,
                        **({"tool_calls": tool_calls} if tool_calls else {}),
                    },
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": prompt_tokens + output_tokens,
            },
        }

    async def stream(
        self, endpoint: ModelEndpoint, request: ChatCompletionRequest
    ) -> AsyncIterator[dict[str, Any]]:
        completion = await self.chat(endpoint, request)
        choice = completion["choices"][0]
        message = choice["message"]
        tool_calls = message.get("tool_calls")
        if tool_calls:
            yield {
                "id": completion["id"],
                "object": "chat.completion.chunk",
                "created": completion["created"],
                "model": request.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "tool_calls": tool_calls},
                        "finish_reason": None,
                    }
                ],
            }
            yield {
                "id": completion["id"],
                "object": "chat.completion.chunk",
                "created": completion["created"],
                "model": request.model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            }
            return
        content = str(message.get("content") or "")
        for token in content.split(" "):
            yield {
                "id": completion["id"],
                "object": "chat.completion.chunk",
                "created": completion["created"],
                "model": request.model,
                "choices": [{"index": 0, "delta": {"content": token + " "}, "finish_reason": None}],
            }
        yield {
            "id": completion["id"],
            "object": "chat.completion.chunk",
            "created": completion["created"],
            "model": request.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
        }

    async def count_tokens(self, endpoint: ModelEndpoint, request: ChatCompletionRequest) -> int:
        del endpoint
        return max(1, sum(len(message.text()) for message in request.messages) // 4)
