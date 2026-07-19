import uuid
from decimal import Decimal

from ecoroute.api.schemas import (
    PRESET_CONFIG,
    ChatCompletionRequest,
    RouterClassification,
    RoutingPolicyConfig,
    RoutingWeights,
)
from ecoroute.routing.engine import EndpointCandidate, select_candidate
from ecoroute.routing.safety import normalize_request


def endpoint(
    name: str,
    tier: str,
    intensity: float,
    cost: str,
    *,
    profile: bool = False,
    latency: int = 200,
    capabilities: set[str] | None = None,
    context_window: int = 32768,
    enabled: bool = True,
    health: str = "healthy",
    region: str = "unknown",
    self_hosted: bool = False,
    carbon_available: bool = True,
    energy_evidence: str = "simulated",
    carbon_evidence: str = "simulated",
    processing_location_evidence: str = "simulated",
    grid_attribution: str = "simulated",
    routing_grid_intensity: float | None = None,
) -> EndpointCandidate:
    return EndpointCandidate(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        name=name,
        provider="fake",
        quality_tier=tier,
        capabilities=capabilities or {"text", "tools"},
        context_window_tokens=context_window,
        input_usd_per_million_tokens=Decimal(cost),
        output_usd_per_million_tokens=Decimal(cost),
        fixed_request_kwh={"frontier": 0.001, "small": 0.0002, "specialized": 0.0001}[tier],
        input_kwh_per_1k_tokens=0.001,
        output_kwh_per_1k_tokens=0.002,
        energy_evidence=energy_evidence,
        latency_p95_ms=latency,
        grid_intensity=intensity,
        enabled=enabled,
        health_state=health,
        slm_profile_id=uuid.uuid4() if profile else None,
        carbon_available=carbon_available,
        region=region,
        self_hosted=self_hosted,
        carbon_evidence=carbon_evidence,
        processing_location_evidence=processing_location_evidence,
        grid_attribution=grid_attribution,
        routing_grid_intensity=routing_grid_intensity,
    )


def context():
    request = ChatCompletionRequest(
        model="support-default",
        messages=[{"role": "user", "content": "What is the return window?"}],
    )
    return normalize_request(uuid.uuid4(), request)


def low() -> RouterClassification:
    return RouterClassification(
        complexity="low",
        task_type="policy_qa",
        risk="low",
        slm_eligible=True,
        cache_eligible=True,
        required_capabilities=["text"],
        predicted_output_tokens=80,
        confidence=0.98,
        rationale_code="PUBLIC_POLICY_LOOKUP",
    )


def test_dirty_grid_prefers_approved_specialized() -> None:
    baseline = endpoint("frontier", "frontier", 650, "8")
    specialized = endpoint("support", "specialized", 650, "0.3", profile=True)
    small = endpoint("small", "small", 650, "0.6")
    weights, _ = PRESET_CONFIG["eco"]
    policy = RoutingPolicyConfig(
        preset="eco",
        max_cost_increase_pct=0,
        weights=weights,
        enabled_endpoint_ids=[baseline.id, specialized.id, small.id],
    )
    selected, _, reason = select_candidate(
        [baseline, small, specialized], context(), low(), policy, baseline, baseline.id
    )
    assert selected.id == specialized.id
    assert reason == "dirty_grid_specialized_preference"


def test_live_demo_routing_signal_does_not_claim_endpoint_carbon() -> None:
    baseline = endpoint(
        "global-frontier",
        "frontier",
        275,
        "8",
        carbon_available=False,
        routing_grid_intensity=650,
    )
    specialized = endpoint(
        "support",
        "specialized",
        275,
        "0.3",
        profile=True,
        carbon_available=False,
    )
    policy = RoutingPolicyConfig(enabled_endpoint_ids=[baseline.id, specialized.id])

    selected, snapshots, reason = select_candidate(
        [baseline, specialized], context(), low(), policy, baseline, baseline.id
    )

    assert selected.id == specialized.id
    assert reason == "dirty_grid_specialized_preference"
    assert all(snapshot.estimated_carbon_g is None for snapshot in snapshots)


def test_clean_grid_uses_general_small_route() -> None:
    baseline = endpoint("frontier", "frontier", 100, "8")
    specialized = endpoint("support", "specialized", 100, "0.3", profile=True)
    small = endpoint("small", "small", 100, "0.6")
    policy = RoutingPolicyConfig(
        enabled_endpoint_ids=[baseline.id, specialized.id, small.id],
    )
    selected, snapshots, _ = select_candidate(
        [baseline, small, specialized], context(), low(), policy, baseline, baseline.id
    )
    assert selected.id == small.id
    specialized_snapshot = next(item for item in snapshots if item.endpoint_id == specialized.id)
    assert specialized_snapshot.excluded_reason == "specialized_reserved_for_dirty_grid"


def test_high_risk_excludes_small() -> None:
    baseline = endpoint("frontier", "frontier", 650, "8")
    small = endpoint("small", "small", 650, "0.6")
    classification = low().model_copy(
        update={"complexity": "high", "risk": "high", "slm_eligible": False}
    )
    policy = RoutingPolicyConfig(enabled_endpoint_ids=[baseline.id, small.id])
    selected, snapshots, _ = select_candidate(
        [small, baseline], context(), classification, policy, baseline, baseline.id
    )
    assert selected.id == baseline.id
    assert (
        next(item for item in snapshots if item.endpoint_id == small.id).excluded_reason
        == "frontier_required"
    )


def test_eco_excludes_cost_increase() -> None:
    baseline = endpoint("frontier", "frontier", 650, "1")
    greener = endpoint("green", "small", 80, "2")
    weights, _ = PRESET_CONFIG["eco"]
    policy = RoutingPolicyConfig(
        preset="eco",
        weights=weights,
        max_cost_increase_pct=0,
        enabled_endpoint_ids=[baseline.id, greener.id],
    )
    _, snapshots, _ = select_candidate(
        [baseline, greener], context(), low(), policy, baseline, baseline.id
    )
    assert (
        next(item for item in snapshots if item.endpoint_id == greener.id).excluded_reason
        == "cost_limit_exceeded"
    )


def test_unknown_carbon_never_looks_like_zero_emissions() -> None:
    unknown = endpoint("unknown-carbon", "small", 0, "1", carbon_available=False, latency=100)
    known = endpoint("known-carbon", "small", 200, "1", latency=100)
    policy = RoutingPolicyConfig(
        preset="custom",
        max_cost_increase_pct=100,
        weights=RoutingWeights(carbon=1, cost=0, latency=0, quality=0, evidence=0),
        enabled_endpoint_ids=[unknown.id, known.id],
    )
    selected, snapshots, _ = select_candidate(
        [unknown, known], context(), low(), policy, unknown, known.id
    )
    assert selected.id == known.id
    assert (
        next(item for item in snapshots if item.endpoint_id == unknown.id).estimated_carbon_g
        is None
    )


def test_equal_routes_prefer_stronger_location_and_grid_evidence() -> None:
    verified = endpoint(
        "verified-location",
        "small",
        200,
        "1",
        energy_evidence="estimated",
        carbon_evidence="measured",
        processing_location_evidence="provider_contract",
        grid_attribution="electricity_maps_data_center",
    )
    declared = endpoint(
        "declared-location",
        "small",
        200,
        "1",
        energy_evidence="estimated",
        carbon_evidence="measured",
        processing_location_evidence="operator_declared",
        grid_attribution="operator_declared",
    )
    policy = RoutingPolicyConfig(
        preset="custom",
        max_cost_increase_pct=100,
        weights=RoutingWeights(carbon=0, cost=0, latency=0, quality=0, evidence=1),
        enabled_endpoint_ids=[verified.id, declared.id],
    )

    selected, snapshots, _ = select_candidate(
        [declared, verified], context(), low(), policy, declared, declared.id
    )

    assert selected.id == verified.id
    verified_snapshot = next(item for item in snapshots if item.endpoint_id == verified.id)
    assert verified_snapshot.grid_attribution == "electricity_maps_data_center"


def test_routing_constraints_fail_closed_with_specific_reasons() -> None:
    baseline = endpoint("baseline", "frontier", 200, "1", self_hosted=True)
    variants = [
        endpoint("disabled", "small", 200, "1", enabled=False),
        endpoint("unhealthy", "small", 200, "1", health="unhealthy"),
        endpoint("capability", "small", 200, "1", capabilities={"text"}),
        endpoint("context", "small", 200, "1", context_window=2),
        endpoint("latency", "small", 200, "1", latency=40000),
        endpoint("region", "small", 200, "1", region="eu-west"),
        endpoint("privacy", "small", 200, "1", self_hosted=False),
    ]
    classification = low().model_copy(update={"required_capabilities": ["text", "tools"]})
    sensitive = context().model_copy(update={"contains_pii": True})
    policy = RoutingPolicyConfig(
        max_cost_increase_pct=100,
        allowed_regions=["unknown"],
        sensitive_requires_self_hosted=True,
        enabled_endpoint_ids=[baseline.id, *(item.id for item in variants)],
    )
    _, snapshots, _ = select_candidate(
        [*variants, baseline], sensitive, classification, policy, baseline, baseline.id
    )
    reasons = {item.name: item.excluded_reason for item in snapshots}
    assert reasons == {
        "disabled": "endpoint_disabled",
        "unhealthy": "endpoint_unhealthy",
        "capability": "privacy_requires_self_hosted",
        "context": "privacy_requires_self_hosted",
        "latency": "privacy_requires_self_hosted",
        "region": "region_not_allowed",
        "privacy": "privacy_requires_self_hosted",
        "baseline": None,
    }


def test_individual_capability_context_and_latency_constraints() -> None:
    baseline = endpoint("baseline-constraints", "frontier", 200, "1")
    variants = [
        endpoint("capability-only", "small", 200, "1", capabilities={"text"}),
        endpoint("context-only", "small", 200, "1", context_window=2),
        endpoint("latency-only", "small", 200, "1", latency=40000),
    ]
    policy = RoutingPolicyConfig(
        max_cost_increase_pct=100,
        enabled_endpoint_ids=[baseline.id, *(item.id for item in variants)],
    )
    classification = low().model_copy(update={"required_capabilities": ["text", "tools"]})
    _, snapshots, _ = select_candidate(
        [*variants, baseline], context(), classification, policy, baseline, baseline.id
    )
    reasons = {item.name: item.excluded_reason for item in snapshots}
    assert reasons["capability-only"] == "capability_mismatch"
    # Context and latency candidates also lack no required capability because their
    # helper defaults include tools, so their own limits are reached next.
    assert reasons["context-only"] == "context_window_exceeded"
    assert reasons["latency-only"] == "latency_limit_exceeded"


def test_unknown_health_is_eligible_only_without_healthy_candidates() -> None:
    unknown = endpoint("health-unknown", "frontier", 200, "1", health="unknown")
    policy = RoutingPolicyConfig(enabled_endpoint_ids=[unknown.id])
    selected, snapshots, _ = select_candidate(
        [unknown], context(), low(), policy, unknown, unknown.id
    )
    assert selected.id == unknown.id
    assert snapshots[0].excluded_reason is None

    healthy = endpoint("health-healthy", "frontier", 200, "1")
    policy = RoutingPolicyConfig(enabled_endpoint_ids=[unknown.id, healthy.id])
    selected, snapshots, _ = select_candidate(
        [unknown, healthy], context(), low(), policy, healthy, healthy.id
    )
    assert selected.id == healthy.id
    assert (
        next(item for item in snapshots if item.endpoint_id == unknown.id).excluded_reason
        == "endpoint_health_unknown"
    )


def test_unknown_health_is_allowed_when_healthy_endpoint_cannot_serve_request() -> None:
    unknown = endpoint(
        "unknown-tools", "frontier", 200, "1", health="unknown", capabilities={"text", "tools"}
    )
    healthy = endpoint("healthy-text-only", "frontier", 200, "1", capabilities={"text"})
    policy = RoutingPolicyConfig(enabled_endpoint_ids=[unknown.id, healthy.id])
    classification = low().model_copy(update={"required_capabilities": ["text", "tools"]})
    selected, snapshots, _ = select_candidate(
        [healthy, unknown], context(), classification, policy, healthy, unknown.id
    )
    assert selected.id == unknown.id
    assert (
        next(item for item in snapshots if item.endpoint_id == unknown.id).excluded_reason is None
    )


def test_configured_fallback_cannot_bypass_hard_safety_filters() -> None:
    fallback = endpoint("disabled-fallback", "frontier", 200, "1", enabled=False)
    policy = RoutingPolicyConfig(enabled_endpoint_ids=[fallback.id])
    try:
        select_candidate([fallback], context(), low(), policy, fallback, fallback.id)
    except ValueError as exc:
        assert "endpoint_disabled" in str(exc)
    else:
        raise AssertionError("disabled fallback was selected")


def test_scores_within_one_hundredth_use_cost_tiebreaker() -> None:
    cleaner_expensive = endpoint("cleaner-expensive", "small", 100, "2", latency=100)
    dirtier_cheaper = endpoint("dirtier-cheaper", "small", 200, "1", latency=100)
    policy = RoutingPolicyConfig(
        preset="custom",
        max_cost_increase_pct=100,
        weights=RoutingWeights(carbon=0.009, cost=0, latency=0, quality=0.991, evidence=0),
        enabled_endpoint_ids=[cleaner_expensive.id, dirtier_cheaper.id],
    )
    selected, _, _ = select_candidate(
        [cleaner_expensive, dirtier_cheaper],
        context(),
        low(),
        policy,
        cleaner_expensive,
        cleaner_expensive.id,
    )
    assert selected.id == dirtier_cheaper.id


def test_ties_are_deterministic() -> None:
    first = endpoint("tie-a", "small", 200, "1", latency=100)
    second = endpoint("tie-b", "small", 200, "1", latency=100)
    policy = RoutingPolicyConfig(enabled_endpoint_ids=[first.id, second.id])
    expected = min((first, second), key=lambda item: str(item.id))
    selections = [
        select_candidate(order, context(), low(), policy, first, first.id)[0].id
        for order in ([first, second], [second, first])
    ]
    assert selections == [expected.id, expected.id]
