import uuid
from typing import Any

import pytest
from ecoroute.api.schemas import ChatCompletionRequest
from ecoroute.config import Settings
from ecoroute.routing import classifier as classifier_module
from ecoroute.routing.classifier import classify as classify_features
from ecoroute.routing.classifier import deterministic_classify
from ecoroute.routing.safety import normalize_request


def classify(text: str, **kwargs):
    request = ChatCompletionRequest(
        model="support-default", messages=[{"role": "user", "content": text}], **kwargs
    )
    return deterministic_classify(normalize_request(uuid.uuid4(), request))


def test_public_policy_is_low_and_slm_eligible() -> None:
    result = classify("What is the return window for unused items?")
    assert result.complexity == "low"
    assert result.task_type == "policy_qa"
    assert result.slm_eligible and result.cache_eligible


def test_legal_is_high_and_frontier_only() -> None:
    result = classify("Compare the policy with Ontario law and assess lawsuit risk")
    assert result.complexity == "high" and result.risk == "high"
    assert result.task_type == "legal"
    assert not result.slm_eligible and not result.cache_eligible


def test_tool_overrides_other_content() -> None:
    result = classify(
        "What is the return window?",
        tools=[{"type": "function", "function": {"name": "refund", "parameters": {}}}],
    )
    assert result.risk == "high"
    assert "tools" in result.required_capabilities
    assert not result.slm_eligible


def test_personalized_order_is_not_cached_or_slm_routed() -> None:
    result = classify("My order NS-1234 has not arrived")
    assert result.task_type == "order_support"
    assert not result.slm_eligible and not result.cache_eligible


@pytest.mark.asyncio
async def test_learned_router_cannot_override_personalized_safety(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"complexity":"low","task_type":"policy_qa","risk":"low",'
                                '"slm_eligible":true,"cache_eligible":true,'
                                '"required_capabilities":["text"],"predicted_output_tokens":96,'
                                '"confidence":0.99,"rationale_code":"PUBLIC_POLICY_LOOKUP"}'
                            )
                        }
                    }
                ]
            }

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(classifier_module.httpx, "AsyncClient", lambda **kwargs: Client())
    request = ChatCompletionRequest(
        model="support-default",
        messages=[{"role": "user", "content": "Where is my order NS-1234?"}],
    )
    features = normalize_request(uuid.uuid4(), request)
    settings = Settings(
        FREESOLO_ROUTER_BASE_URL="https://router.example/v1",
        FREESOLO_ROUTER_MODEL_ID="router",
        FREESOLO_API_KEY="configured",
    )
    result = await classify_features(features, settings)
    assert not result.slm_eligible
    assert not result.cache_eligible
    assert result.risk == "medium"
