from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from ecoroute.api.schemas import (
    CandidateSnapshot,
    NormalizedRequestFeatures,
    RouterClassification,
    RoutingPolicyConfig,
)

QUALITY_PENALTY = {"frontier": 0.0, "standard": 0.2, "small": 0.45, "specialized": 0.15}
EVIDENCE_PENALTY = {"measured": 0.0, "estimated": 0.1, "stale": 0.3, "simulated": 0.4}
PROCESSING_LOCATION_PENALTY = {
    "provider_contract": 0.05,
    "operator_declared": 0.3,
    "self_hosted": 0.0,
    "unknown": 0.5,
    "simulated": 0.4,
}
GRID_ATTRIBUTION_PENALTY = {
    "electricity_maps_data_center": 0.0,
    "physical_grid": 0.0,
    "regional_proxy": 0.25,
    "operator_declared": 0.35,
    "unknown": 0.5,
    "simulated": 0.4,
}


@dataclass(frozen=True)
class EndpointCandidate:
    id: uuid.UUID
    name: str
    provider: str
    quality_tier: str
    capabilities: set[str]
    context_window_tokens: int
    input_usd_per_million_tokens: Decimal
    output_usd_per_million_tokens: Decimal
    fixed_request_kwh: float
    input_kwh_per_1k_tokens: float
    output_kwh_per_1k_tokens: float
    energy_evidence: str
    latency_p95_ms: int
    grid_intensity: float
    enabled: bool
    health_state: str
    slm_profile_id: uuid.UUID | None
    carbon_available: bool = True
    region: str = "unknown"
    self_hosted: bool = False
    slm_profile_status: str | None = "ready"
    allowed_task_types: frozenset[str] = frozenset(
        {"policy_qa", "summarization", "classification", "extraction", "reply_draft"}
    )
    supported_languages: frozenset[str] = frozenset({"en"})
    grid_zone: str = "unknown"
    carbon_evidence: str = "estimated"
    carbon_source: str = "unknown"
    processing_location_evidence: str = "unknown"
    grid_attribution: str = "unknown"
    azure_deployment_type: str | None = None


def evidence_penalty(candidate: EndpointCandidate) -> float:
    return max(
        EVIDENCE_PENALTY.get(candidate.energy_evidence, 0.5),
        EVIDENCE_PENALTY.get(candidate.carbon_evidence, 0.5),
        PROCESSING_LOCATION_PENALTY.get(candidate.processing_location_evidence, 0.5),
        GRID_ATTRIBUTION_PENALTY.get(candidate.grid_attribution, 0.5),
    )


def estimate_cost(candidate: EndpointCandidate, input_tokens: int, output_tokens: int) -> Decimal:
    return (
        Decimal(input_tokens) * candidate.input_usd_per_million_tokens
        + Decimal(output_tokens) * candidate.output_usd_per_million_tokens
    ) / Decimal(1_000_000)


def estimate_energy(candidate: EndpointCandidate, input_tokens: int, output_tokens: int) -> float:
    return (
        candidate.fixed_request_kwh
        + input_tokens * candidate.input_kwh_per_1k_tokens / 1000
        + output_tokens * candidate.output_kwh_per_1k_tokens / 1000
    )


def grid_state(intensity: float | None, clean: float = 150, dirty: float = 400) -> str:
    if intensity is None:
        return "unknown"
    if intensity <= clean:
        return "clean"
    if intensity >= dirty:
        return "dirty"
    return "moderate"


def exact_cache_eligible(
    features: NormalizedRequestFeatures,
    policy: RoutingPolicyConfig,
    classification: RouterClassification | None = None,
) -> bool:
    return bool(
        not features.has_tools
        and not features.has_multimodal
        and not features.contains_pii
        and not features.contains_secrets
        and not features.is_personalized
        and not features.detection_uncertain
        and features.deterministic
        and policy.cache_ttl_seconds > 0
        and (classification is None or classification.risk != "high")
        and (classification is None or classification.cache_eligible)
    )


def semantic_cache_eligible(
    features: NormalizedRequestFeatures,
    policy: RoutingPolicyConfig,
    classification: RouterClassification | None = None,
) -> bool:
    return bool(
        exact_cache_eligible(features, policy, classification)
        and policy.semantic_cache_enabled
        and features.assistant_turn_count <= 1
        and (classification is None or classification.task_type in policy.semantic_cache_task_types)
    )


def _normalized(values: list[float]) -> list[float]:
    low, high = min(values), max(values)
    if high - low < 1e-12:
        return [0.0] * len(values)
    return [(value - low) / (high - low) for value in values]


def select_candidate(
    candidates: list[EndpointCandidate],
    features: NormalizedRequestFeatures,
    classification: RouterClassification,
    policy: RoutingPolicyConfig,
    baseline: EndpointCandidate,
    fallback_id: uuid.UUID,
) -> tuple[EndpointCandidate, list[CandidateSnapshot], str]:
    predicted_output = classification.predicted_output_tokens
    baseline_cost = estimate_cost(baseline, features.input_token_estimate, predicted_output)
    snapshots: list[CandidateSnapshot] = []
    eligible: list[tuple[EndpointCandidate, CandidateSnapshot]] = []
    required = set(classification.required_capabilities)
    matching_rule = next(
        (
            rule
            for rule in policy.task_rules
            if rule.get("taskType", rule.get("task_type")) == classification.task_type
        ),
        {},
    )
    quality_rank = {"specialized": 1, "small": 1, "standard": 2, "frontier": 3}
    minimum_quality = matching_rule.get(
        "minimumQualityTier", matching_rule.get("minimum_quality_tier")
    )

    for candidate in candidates:
        cost = estimate_cost(candidate, features.input_token_estimate, predicted_output)
        energy = estimate_energy(candidate, features.input_token_estimate, predicted_output)
        snapshot = CandidateSnapshot(
            endpoint_id=candidate.id,
            name=candidate.name,
            provider=candidate.provider,
            quality_tier=candidate.quality_tier,
            estimated_energy_kwh=energy,
            estimated_cost_usd=cost,
            estimated_carbon_g=(
                energy * candidate.grid_intensity if candidate.carbon_available else None
            ),
            latency_p95_ms=candidate.latency_p95_ms,
            evidence=candidate.energy_evidence,
            region=candidate.region,
            azure_deployment_type=candidate.azure_deployment_type,
            grid_zone=candidate.grid_zone,
            carbon_evidence=candidate.carbon_evidence,
            processing_location_evidence=candidate.processing_location_evidence,
            grid_attribution=candidate.grid_attribution,
            carbon_source=candidate.carbon_source,
        )
        reason: str | None = None
        if not candidate.enabled:
            reason = "endpoint_disabled"
        elif candidate.health_state == "unhealthy":
            reason = "endpoint_unhealthy"
        elif policy.enabled_endpoint_ids and candidate.id not in policy.enabled_endpoint_ids:
            reason = "policy_not_allowed"
        elif policy.allowed_regions and candidate.region not in policy.allowed_regions:
            reason = "region_not_allowed"
        elif (
            policy.sensitive_requires_self_hosted
            and (features.contains_pii or features.is_personalized)
            and not candidate.self_hosted
        ):
            reason = "privacy_requires_self_hosted"
        elif not required.issubset(candidate.capabilities):
            reason = "capability_mismatch"
        elif minimum_quality and quality_rank.get(candidate.quality_tier, 0) < quality_rank.get(
            str(minimum_quality), 0
        ):
            reason = "minimum_quality_tier"
        elif features.input_token_estimate + predicted_output > candidate.context_window_tokens:
            reason = "context_window_exceeded"
        elif candidate.latency_p95_ms > policy.max_latency_ms:
            reason = "latency_limit_exceeded"
        elif baseline_cost and cost > baseline_cost * (
            Decimal(1) + Decimal(str(policy.max_cost_increase_pct)) / 100
        ):
            reason = "cost_limit_exceeded"
        elif classification.risk == "high" or classification.complexity == "high":
            if candidate.quality_tier != "frontier":
                reason = "frontier_required"
        elif candidate.quality_tier == "specialized" and (
            not classification.slm_eligible
            or candidate.slm_profile_id is None
            or candidate.slm_profile_status not in {"ready", "active", "deployed", "experimental"}
        ):
            reason = "specialized_domain_mismatch"
        elif candidate.quality_tier == "specialized" and (
            classification.task_type not in candidate.allowed_task_types
            or features.requested_language not in candidate.supported_languages
        ):
            reason = "specialized_task_not_allowed"
        elif (
            candidate.quality_tier == "specialized"
            and candidate.slm_profile_status == "experimental"
            and not policy.allow_experimental_models
        ):
            reason = "experimental_model_not_allowed"
        snapshot.excluded_reason = reason
        snapshots.append(snapshot)
        if reason is None:
            eligible.append((candidate, snapshot))

    # Unknown health is allowed only when no otherwise-eligible known-health
    # alternative exists. A healthy endpoint that fails capabilities/privacy is
    # not an alternative for this request.
    if any(candidate.health_state != "unknown" for candidate, _ in eligible):
        retained: list[tuple[EndpointCandidate, CandidateSnapshot]] = []
        for candidate, snapshot in eligible:
            if candidate.health_state == "unknown":
                snapshot.excluded_reason = "endpoint_health_unknown"
            else:
                retained.append((candidate, snapshot))
        eligible = retained

    if not eligible:
        fallback = next(
            (candidate for candidate in candidates if candidate.id == fallback_id), None
        )
        if fallback is None:
            raise ValueError("no eligible endpoint and configured fallback is unavailable")
        fallback_snapshot = next(item for item in snapshots if item.endpoint_id == fallback.id)
        raise ValueError(
            "no eligible endpoint; configured fallback was excluded"
            + (
                f" ({fallback_snapshot.excluded_reason})"
                if fallback_snapshot.excluded_reason
                else ""
            )
        )

    # Dirty-grid support traffic has an explicit in-domain specialized preference.
    local_grid = grid_state(
        baseline.grid_intensity if baseline.carbon_available else None,
        policy.clean_threshold_gco2_kwh,
        policy.dirty_threshold_gco2_kwh,
    )
    if local_grid == "dirty" and classification.complexity in {"low", "medium"}:
        specialized = [item for item in eligible if item[0].quality_tier == "specialized"]
        if specialized:
            selected = sorted(
                specialized, key=lambda item: (item[1].estimated_cost_usd, str(item[0].id))
            )[0]
            selected[1].score = 0
            return selected[0], snapshots, "dirty_grid_specialized_preference"

    if local_grid != "dirty" and classification.complexity in {"low", "medium"}:
        general_candidates = [item for item in eligible if item[0].quality_tier != "specialized"]
        if general_candidates:
            for candidate, snapshot in eligible:
                if candidate.quality_tier == "specialized":
                    snapshot.excluded_reason = "specialized_reserved_for_dirty_grid"
            eligible = general_candidates

    available_carbon_values = [
        snapshot.estimated_carbon_g
        for candidate, snapshot in eligible
        if candidate.carbon_available and snapshot.estimated_carbon_g is not None
    ]
    normalized_available = _normalized(available_carbon_values) if available_carbon_values else []
    available_iter = iter(normalized_available)
    # Missing carbon data is never evidence of zero emissions. If at least one
    # candidate has a real reading, conservatively score unknown readings at the
    # worst end of the carbon dimension; if none do, carbon has no effect.
    carbons = [
        next(available_iter)
        if candidate.carbon_available
        else (1.0 if normalized_available else 0.0)
        for candidate, _ in eligible
    ]
    costs = _normalized([float(item[1].estimated_cost_usd) for item in eligible])
    latencies = _normalized([float(item[1].latency_p95_ms) for item in eligible])
    for index, (candidate, snapshot) in enumerate(eligible):
        snapshot.score = (
            policy.weights.carbon * carbons[index]
            + policy.weights.cost * costs[index]
            + policy.weights.latency * latencies[index]
            + policy.weights.quality * QUALITY_PENALTY[candidate.quality_tier]
            + policy.weights.evidence * evidence_penalty(candidate)
        )
    minimum_score = min(item[1].score or 0 for item in eligible)
    tied = [item for item in eligible if (item[1].score or 0) - minimum_score <= 0.01 + 1e-12]
    tied.sort(
        key=lambda item: (
            item[1].estimated_cost_usd,
            item[1].latency_p95_ms,
            str(item[0].id),
        )
    )
    return tied[0][0], snapshots, "weighted_score"
