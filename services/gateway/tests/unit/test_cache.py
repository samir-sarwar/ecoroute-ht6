import uuid

from ecoroute.api.schemas import ChatCompletionRequest, RoutingPolicyConfig
from ecoroute.cache.embeddings import LocalEmbedder, cosine_similarity
from ecoroute.cache.fingerprint import exact_fingerprint
from ecoroute.cache.service import normalize_semantic_text
from ecoroute.routing.engine import exact_cache_eligible, semantic_cache_eligible
from ecoroute.routing.safety import normalize_request


def test_fingerprint_ignores_audit_metadata_and_is_stable() -> None:
    workspace = uuid.uuid4()
    request1 = ChatCompletionRequest(
        model="support-default",
        messages=[{"role": "user", "content": "Returns?"}],
        response_format={"type": "json_object", "schema": {"b": 2, "a": 1}},
        metadata={"demo_session_id": "one"},
    )
    request2 = ChatCompletionRequest(
        model="support-default",
        messages=[{"content": "Returns?", "role": "user"}],
        response_format={"schema": {"a": 1, "b": 2}, "type": "json_object"},
        metadata={"demo_session_id": "two"},
    )
    first = normalize_request(uuid.uuid4(), request1)
    second = normalize_request(uuid.uuid4(), request2)
    assert exact_fingerprint(workspace, request1, first, 1) == exact_fingerprint(
        workspace, request2, second, 1
    )


def test_cache_fails_closed_for_personalized_or_nondeterministic() -> None:
    personalized = ChatCompletionRequest(
        model="support-default",
        messages=[{"role": "user", "content": "Where is my order NS-1234?"}],
    )
    random_request = ChatCompletionRequest(
        model="support-default",
        messages=[{"role": "user", "content": "What is the return window?"}],
        temperature=0.7,
    )
    policy = RoutingPolicyConfig()
    assert not exact_cache_eligible(normalize_request(uuid.uuid4(), personalized), policy)
    assert not exact_cache_eligible(normalize_request(uuid.uuid4(), random_request), policy)


def test_demo_paraphrase_has_high_local_similarity() -> None:
    embedder = LocalEmbedder("unused")
    first = embedder.encode("What is the return window for an unused item?")
    second = embedder.encode("How many days do I have to send back something unused?")
    assert cosine_similarity(first, second) >= 0.94


def test_semantic_normalization_collapses_safe_return_window_paraphrase() -> None:
    assert normalize_semantic_text("What is your return window for unused items?") == (
        "return window unused item"
    )
    assert (
        normalize_semantic_text("How many days do I have to send back something unused?")
        == "return window unused item"
    )
    assert normalize_semantic_text("Can I return a used item?") == "can i return a used item"


def test_cache_bypasses_tools_multimodal_secrets_and_uncertain_detection() -> None:
    policy = RoutingPolicyConfig()
    requests = [
        ChatCompletionRequest(
            model="support-default",
            messages=[{"role": "user", "content": "Look this up"}],
            tools=[{"type": "function", "function": {"name": "lookup"}}],
        ),
        ChatCompletionRequest(
            model="support-default",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this"},
                        {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
                    ],
                }
            ],
        ),
        ChatCompletionRequest(
            model="support-default",
            messages=[{"role": "user", "content": "api_key=sk-secretvalue"}],
        ),
        ChatCompletionRequest(
            model="support-default",
            messages=[{"role": "user", "content": "Number 1234567890123"}],
        ),
    ]
    for request in requests:
        assert not exact_cache_eligible(normalize_request(uuid.uuid4(), request), policy)


def test_fingerprint_isolates_workspace_model_context_and_namespace() -> None:
    workspace = uuid.uuid4()
    request = ChatCompletionRequest(
        model="support-default",
        messages=[
            {"role": "system", "content": "Tenant A policy"},
            {"role": "user", "content": "Returns?"},
        ],
    )
    features = normalize_request(uuid.uuid4(), request)
    base = exact_fingerprint(workspace, request, features, 1)
    other_workspace = exact_fingerprint(uuid.uuid4(), request, features, 1)
    other_model_request = request.model_copy(update={"model": "other-model"})
    other_model = exact_fingerprint(workspace, other_model_request, features, 1)
    other_context_request = ChatCompletionRequest(
        model="support-default",
        messages=[
            {"role": "system", "content": "Tenant B policy"},
            {"role": "user", "content": "Returns?"},
        ],
    )
    other_context_features = normalize_request(uuid.uuid4(), other_context_request)
    other_context = exact_fingerprint(workspace, other_context_request, other_context_features, 1)
    other_namespace = exact_fingerprint(workspace, request, features, 2)
    assert len({base, other_workspace, other_model, other_context, other_namespace}) == 5


def test_fingerprint_uses_normalized_text_messages() -> None:
    workspace = uuid.uuid4()
    first = ChatCompletionRequest(
        model="support-default",
        messages=[{"role": "user", "content": "  What\t is  the return window?  "}],
    )
    second = ChatCompletionRequest(
        model="support-default",
        messages=[{"role": "user", "content": "What is the return window?"}],
    )
    first_features = normalize_request(uuid.uuid4(), first)
    second_features = normalize_request(uuid.uuid4(), second)
    assert exact_fingerprint(workspace, first, first_features, 1) == exact_fingerprint(
        workspace, second, second_features, 1
    )


def test_semantic_cache_rejects_long_assistant_histories() -> None:
    request = ChatCompletionRequest(
        model="support-default",
        messages=[
            {"role": "user", "content": "Returns?"},
            {"role": "assistant", "content": "Thirty days."},
            {"role": "user", "content": "Final sale?"},
            {"role": "assistant", "content": "Usually excluded."},
            {"role": "user", "content": "Defective?"},
        ],
    )
    assert not semantic_cache_eligible(
        normalize_request(uuid.uuid4(), request), RoutingPolicyConfig()
    )
