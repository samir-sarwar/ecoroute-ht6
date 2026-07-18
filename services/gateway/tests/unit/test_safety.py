import uuid

import pytest
from ecoroute.api.schemas import ChatCompletionRequest, ModelEndpointCreate
from ecoroute.routing.safety import luhn_valid, normalize_request
from pydantic import ValidationError


def features(text: str):
    return normalize_request(
        uuid.uuid4(),
        ChatCompletionRequest(
            model="support-default", messages=[{"role": "user", "content": text}]
        ),
    )


def test_luhn_and_card_redaction() -> None:
    assert luhn_valid("4242 4242 4242 4242")
    value = features("Charge card 4242 4242 4242 4242")
    assert value.contains_pii
    assert "[CARD]" in value.redacted_preview


def test_invalid_card_like_number_is_uncertain_not_card() -> None:
    value = features("number 1234 5678 9012 3456")
    assert value.detection_uncertain
    assert "[CARD]" not in value.redacted_preview


def test_pii_secrets_and_personalization() -> None:
    value = features("My order NS-12345 is for me@example.com and token=abcdefghi")
    assert value.contains_pii and value.contains_secrets and value.is_personalized
    assert "[ORDER_ID]" in value.redacted_preview
    assert "[EMAIL]" in value.redacted_preview
    assert "[SECRET]" in value.redacted_preview


def test_multimodal_feature() -> None:
    request = ChatCompletionRequest(
        model="support-default",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            }
        ],
    )
    assert normalize_request(uuid.uuid4(), request).has_multimodal


def test_semantic_text_is_final_user_turn_and_prior_context_is_bound() -> None:
    first = ChatCompletionRequest.model_validate(
        {
            "model": "support-default",
            "messages": [
                {"role": "system", "content": "Answer from public policy."},
                {"role": "user", "content": "How do returns work?"},
            ],
        }
    )
    follow_up = ChatCompletionRequest.model_validate(
        {
            "model": "support-default",
            "messages": [
                {"role": "system", "content": "Answer from public policy."},
                {"role": "user", "content": "How do returns work?"},
                {"role": "assistant", "content": "Returns are accepted for 30 days."},
                {"role": "user", "content": "How long does shipping take?"},
            ],
        }
    )

    first_features = normalize_request(uuid.uuid4(), first)
    follow_up_features = normalize_request(uuid.uuid4(), follow_up)

    assert first_features.normalized_text == "How do returns work?"
    assert follow_up_features.normalized_text == "How long does shipping take?"
    assert first_features.system_prompt_hash != follow_up_features.system_prompt_hash


def test_identical_prior_context_produces_identical_context_hash() -> None:
    payload = {
        "model": "support-default",
        "messages": [
            {"role": "system", "content": "Answer from public policy."},
            {"role": "user", "content": "How do returns work?"},
            {"role": "assistant", "content": "Returns are accepted for 30 days."},
            {"role": "user", "content": "How long does shipping take?"},
        ],
    }
    first = normalize_request(uuid.uuid4(), ChatCompletionRequest.model_validate(payload))
    second = normalize_request(uuid.uuid4(), ChatCompletionRequest.model_validate(payload))

    assert first.system_prompt_hash == second.system_prompt_hash


def test_sensitive_data_in_prior_user_turn_disables_reuse() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "model": "support-default",
            "messages": [
                {"role": "user", "content": "My email is person@example.com"},
                {"role": "assistant", "content": "How can I help?"},
                {"role": "user", "content": "What is the return policy?"},
            ],
        }
    )

    value = normalize_request(uuid.uuid4(), request)

    assert value.contains_pii
    assert value.normalized_text == "What is the return policy?"
    assert "person@example.com" not in value.redacted_preview


def test_sensitive_data_in_non_user_context_disables_reuse() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "model": "support-default",
            "messages": [
                {"role": "system", "content": "Use api_key=sk-secretvalue"},
                {"role": "assistant", "content": "Contact person@example.com"},
                {"role": "user", "content": "What is the return policy?"},
            ],
        }
    )
    value = normalize_request(uuid.uuid4(), request)
    assert value.contains_secrets
    assert value.contains_pii
    assert value.normalized_text == "What is the return policy?"


def test_unknown_content_part_forces_capability_passthrough() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "model": "support-default",
            "messages": [{"role": "user", "content": [{"vendor_extension": {"value": "opaque"}}]}],
        }
    )
    assert normalize_request(uuid.uuid4(), request).has_multimodal


def endpoint_payload(base_url: str) -> dict[str, object]:
    return {
        "name": "Test",
        "provider": "openai_compatible",
        "baseUrl": base_url,
        "physicalModel": "test",
        "region": "test",
        "gridZone": "test",
        "qualityTier": "frontier",
        "capabilities": ["text"],
        "contextWindowTokens": 4096,
        "inputUsdPerMillionTokens": 0,
        "outputUsdPerMillionTokens": 0,
        "fixedRequestKwh": 0,
        "inputKwhPer1kTokens": 0,
        "outputKwhPer1kTokens": 0,
        "energyEvidence": "estimated",
        "latencyP50Ms": 10,
        "latencyP95Ms": 20,
        "selfHosted": False,
    }


def test_endpoint_url_blocks_metadata_even_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECOROUTE_ENV", "development")
    with pytest.raises(ValidationError, match="metadata endpoint"):
        ModelEndpointCreate.model_validate(
            endpoint_payload("http://metadata.google.internal/latest")
        )


def test_production_private_endpoint_requires_explicit_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECOROUTE_ENV", "production")
    monkeypatch.delenv("ECOROUTE_ALLOWED_ENDPOINT_HOSTS", raising=False)
    with pytest.raises(ValidationError, match="explicit allowlist"):
        ModelEndpointCreate.model_validate(endpoint_payload("http://127.0.0.1:9000/v1"))
    monkeypatch.setenv("ECOROUTE_ALLOWED_ENDPOINT_HOSTS", "127.0.0.1")
    value = ModelEndpointCreate.model_validate(endpoint_payload("http://127.0.0.1:9000/v1"))
    assert value.base_url == "http://127.0.0.1:9000/v1"
