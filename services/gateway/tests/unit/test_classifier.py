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
    assert result.classification_source == "deterministic"


def test_conditional_cleaned_return_is_medium_not_cacheable() -> None:
    result = classify(
        "What can I do if I spilled milk on my shorts, cleaned them, and want to return them?"
    )
    assert result.complexity == "medium"
    assert result.risk == "medium"
    assert result.task_type == "policy_qa"
    assert result.rationale_code == "CONDITIONAL_POLICY_INTERPRETATION"
    assert not result.cache_eligible


def test_benign_unknown_is_medium_and_not_slm_eligible() -> None:
    result = classify("Please help me choose between these two colors")
    assert result.complexity == "medium"
    assert result.risk == "medium"
    assert result.task_type == "unknown"
    assert not result.slm_eligible


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


def test_text_request_to_inspect_upload_and_execute_is_high() -> None:
    result = classify("Analyze the uploaded jacket image and execute an exchange")
    assert result.complexity == "high" and result.risk == "high"
    assert result.task_type == "tool_workflow"
    assert not result.slm_eligible


def test_poisoning_diagnosis_is_high_safety() -> None:
    result = classify("This bottle may have poisoned me; diagnose my symptoms")
    assert result.complexity == "high" and result.risk == "high"
    assert result.task_type == "safety"


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
    assert result.classification_source == "trained_adapter"


@pytest.mark.asyncio
async def test_invalid_learned_output_is_observable_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "not-json"}}]}

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
        messages=[{"role": "user", "content": "What is your return window?"}],
    )
    settings = Settings(
        FREESOLO_ROUTER_BASE_URL="https://router.example/v1",
        FREESOLO_ROUTER_MODEL_ID="router",
        FREESOLO_API_KEY="configured",
    )
    result = await classify_features(normalize_request(uuid.uuid4(), request), settings)
    assert result.complexity == "high" and result.risk == "high"
    assert result.classification_source == "fail_closed"
    assert result.rationale_code == "ROUTER_INVALID_OUTPUT"
