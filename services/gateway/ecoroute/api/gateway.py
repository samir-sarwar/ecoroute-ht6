from __future__ import annotations

import asyncio
import json
import time
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecoroute.api.auth import require_gateway_key
from ecoroute.api.errors import EcoRouteError
from ecoroute.api.events import publish_event
from ecoroute.api.schemas import (
    CarbonReading,
    ChatCompletionRequest,
    QualityVerdict,
    RouterClassification,
    RoutingPolicyConfig,
)
from ecoroute.cache import CacheService, exact_fingerprint
from ecoroute.cache.service import freshen_cached_completion
from ecoroute.carbon.impact import calculate_impact
from ecoroute.carbon.service import CarbonService
from ecoroute.config import get_settings
from ecoroute.db.base import utcnow
from ecoroute.db.models import (
    GatewayRequest,
    ImpactRecord,
    LogicalModel,
    LogicalModelEndpoint,
    ModelAttempt,
    ModelEndpoint,
    RouteDecision,
    RoutingPolicy,
    SlmProfile,
    Workspace,
)
from ecoroute.db.session import SessionLocal, get_redis, get_session
from ecoroute.providers.base import ProviderError
from ecoroute.providers.registry import ProviderRegistry
from ecoroute.routing.classifier import classify, deterministic_classify
from ecoroute.routing.engine import (
    EndpointCandidate,
    exact_cache_eligible,
    select_candidate,
    semantic_cache_eligible,
)
from ecoroute.routing.quality import verify_output
from ecoroute.routing.safety import normalize_request
from ecoroute.telemetry import metrics

router = APIRouter()
settings = get_settings()
providers = ProviderRegistry(settings)
stream_connections = asyncio.Semaphore(settings.max_sse_connections)


async def _redis_text(redis: Redis, key: str, default: str) -> str:
    value = await redis.get(key)
    if isinstance(value, bytes):
        return value.decode()
    return value or default


def _carbon_lookup(endpoint: ModelEndpoint) -> dict[str, str | None]:
    if endpoint.grid_lookup_mode != "data_center":
        return {}
    return {
        "data_center_provider": endpoint.grid_data_center_provider,
        "data_center_region": endpoint.grid_data_center_region,
    }


def _carbon_accounting_available(endpoint: ModelEndpoint, reading: CarbonReading) -> bool:
    if (
        reading.source == "ecoroute-default-no-reading"
        or reading.metadata.get("available") is False
        or endpoint.grid_attribution == "unknown"
        or endpoint.processing_location_evidence == "unknown"
    ):
        return False
    lookup_mode = reading.metadata.get("lookup_mode")
    if endpoint.grid_lookup_mode != "data_center":
        return lookup_mode in {None, "zone"}
    expected_provider = (endpoint.grid_data_center_provider or "").casefold()
    expected_region = (endpoint.grid_data_center_region or "").casefold().replace("_", "-")
    reading_provider = str(reading.metadata.get("data_center_provider") or "").casefold()
    reading_region = (
        str(reading.metadata.get("data_center_region") or "").casefold().replace("_", "-")
    )
    return bool(
        lookup_mode == "data_center"
        and reading_provider == expected_provider
        and reading_region == expected_region
    )


def _claim_scope(endpoint: ModelEndpoint) -> str:
    return {
        "electricity_maps_data_center": "mapped_data_center_grid_operational_estimate",
        "physical_grid": "physical_grid_operational_estimate",
        "regional_proxy": "provider_region_grid_proxy_operational_estimate",
        "operator_declared": "operator_declared_grid_operational_estimate",
        "simulated": "simulated_operational_estimate",
    }.get(endpoint.grid_attribution, "unavailable")


def _candidate(
    endpoint: ModelEndpoint,
    reading: CarbonReading,
    profile: SlmProfile | None = None,
) -> EndpointCandidate:
    carbon_available = _carbon_accounting_available(endpoint, reading)
    return EndpointCandidate(
        id=endpoint.id,
        name=endpoint.name,
        provider=endpoint.provider,
        quality_tier=endpoint.quality_tier,
        capabilities=set(endpoint.capabilities),
        context_window_tokens=endpoint.context_window_tokens,
        input_usd_per_million_tokens=endpoint.input_usd_per_million_tokens,
        output_usd_per_million_tokens=endpoint.output_usd_per_million_tokens,
        fixed_request_kwh=endpoint.fixed_request_kwh,
        input_kwh_per_1k_tokens=endpoint.input_kwh_per_1k_tokens,
        output_kwh_per_1k_tokens=endpoint.output_kwh_per_1k_tokens,
        energy_evidence=endpoint.energy_evidence,
        latency_p95_ms=endpoint.latency_p95_ms,
        grid_intensity=reading.intensity_gco2_kwh,
        region=endpoint.region,
        self_hosted=endpoint.self_hosted,
        enabled=endpoint.enabled,
        health_state=endpoint.health_state,
        slm_profile_id=endpoint.slm_profile_id,
        carbon_available=carbon_available,
        grid_zone=reading.zone,
        carbon_evidence=reading.evidence,
        carbon_source=reading.source,
        processing_location_evidence=endpoint.processing_location_evidence,
        grid_attribution=endpoint.grid_attribution,
        azure_deployment_type=endpoint.azure_deployment_type,
        slm_profile_status=profile.status if profile else None,
        allowed_task_types=frozenset(profile.definition.get("allowed_tasks", []))
        if profile
        else frozenset(),
        supported_languages=frozenset(profile.definition.get("supported_languages", ["en"]))
        if profile
        else frozenset({"en"}),
    )


def _impact_evidence(
    baseline: EndpointCandidate,
    baseline_endpoint: ModelEndpoint,
    baseline_reading: CarbonReading,
    selected: EndpointCandidate,
    selected_endpoint: ModelEndpoint,
    selected_reading: CarbonReading,
) -> dict[str, Any]:
    evidence_rank = {"measured": 0, "estimated": 1, "stale": 2, "simulated": 3}
    carbon_level = max(
        (baseline_reading.evidence, selected_reading.evidence),
        key=lambda value: evidence_rank[value],
    )
    return {
        "energy_level": selected.energy_evidence,
        "carbon_level": carbon_level,
        "energy_source": selected_endpoint.name + ":" + selected_endpoint.coefficient_version,
        "coefficient_version": selected_endpoint.coefficient_version,
        "carbon_source": selected_reading.source,
        "carbon_observed_at": selected_reading.observed_at.isoformat(),
        "carbon_metadata": selected_reading.metadata,
        "grid_zone": selected_reading.zone,
        "endpoint_region": selected_endpoint.region,
        "provider": selected_endpoint.provider,
        "azure_deployment_type": selected_endpoint.azure_deployment_type,
        "processing_location_evidence": selected_endpoint.processing_location_evidence,
        "grid_attribution": selected_endpoint.grid_attribution,
        "baseline_carbon_source": baseline_reading.source,
        "baseline_carbon_observed_at": baseline_reading.observed_at.isoformat(),
        "baseline_carbon_metadata": baseline_reading.metadata,
        "baseline_grid_zone": baseline_reading.zone,
        "baseline_endpoint_region": baseline_endpoint.region,
        "baseline_provider": baseline_endpoint.provider,
        "baseline_azure_deployment_type": baseline_endpoint.azure_deployment_type,
        "baseline_processing_location_evidence": baseline_endpoint.processing_location_evidence,
        "baseline_grid_attribution": baseline_endpoint.grid_attribution,
        "carbon_accounting_available": baseline.carbon_available and selected.carbon_available,
        "attribution_method": "endpoint_coefficients_times_region_grid_intensity",
        "claim_scope": _claim_scope(selected_endpoint),
    }


async def _load_context(
    session: AsyncSession, alias: str, redis: Redis
) -> tuple[Workspace, LogicalModel, RoutingPolicy, RoutingPolicyConfig, list[ModelEndpoint]]:
    del redis
    logical = await session.scalar(
        select(LogicalModel).where(
            LogicalModel.alias == alias,
            LogicalModel.enabled.is_(True),
            LogicalModel.deleted_at.is_(None),
        )
    )
    if logical is None:
        raise EcoRouteError(
            f"The model '{alias}' does not exist",
            status_code=404,
            code="model_not_found",
            param="model",
        )
    workspace = await session.get(Workspace, logical.workspace_id)
    policy = await session.get(RoutingPolicy, logical.active_policy_id)
    if workspace is None or policy is None:
        raise EcoRouteError(
            "Logical model configuration is incomplete",
            status_code=503,
            code="configuration_unavailable",
            error_type="service_unavailable",
        )
    config = RoutingPolicyConfig.model_validate(policy.config)
    endpoint_ids = list(
        (
            await session.scalars(
                select(LogicalModelEndpoint.endpoint_id).where(
                    LogicalModelEndpoint.logical_model_id == logical.id
                )
            )
        ).all()
    )
    endpoints = list(
        (
            await session.scalars(
                select(ModelEndpoint).where(
                    ModelEndpoint.id.in_(endpoint_ids), ModelEndpoint.deleted_at.is_(None)
                )
            )
        ).all()
    )
    if (
        not endpoints
        or logical.required_fallback_endpoint_id is None
        or logical.baseline_endpoint_id is None
    ):
        raise EcoRouteError(
            "No physical endpoint is configured",
            status_code=422,
            code="no_eligible_endpoint",
            error_type="routing_error",
        )
    return workspace, logical, policy, config, endpoints


def _headers(
    request_id: uuid.UUID,
    route: str,
    endpoint_id: uuid.UUID | None,
    cache: str,
    evidence: str,
    fallback: bool,
    *,
    carbon_accounting_available: bool = True,
    grid_attribution: str = "unknown",
    processing_region: str = "unknown",
    provider_deployment_type: str | None = None,
) -> dict[str, str]:
    return {
        "X-Request-Id": str(request_id),
        "X-EcoRoute-Request-Id": str(request_id),
        "X-EcoRoute-Route": route,
        "X-EcoRoute-Endpoint-Id": str(endpoint_id or "cache"),
        "X-EcoRoute-Cache": cache,
        "X-EcoRoute-Evidence": evidence,
        "X-EcoRoute-Fallback": str(fallback).lower(),
        "X-EcoRoute-Carbon-Accounting": (
            "available" if carbon_accounting_available else "unavailable"
        ),
        "X-EcoRoute-Grid-Attribution": grid_attribution,
        "X-EcoRoute-Processing-Region": processing_region,
        "X-EcoRoute-Provider-Deployment": provider_deployment_type or "not-applicable",
    }


def _completion_sse(completion: dict[str, Any], include_usage: bool = False) -> Any:
    async def generate() -> Any:
        choice = completion["choices"][0]
        content = choice["message"].get("content") or ""
        chunk_base = {
            "id": completion["id"],
            "object": "chat.completion.chunk",
            "created": completion["created"],
            "model": completion["model"],
        }
        initial = {
            **chunk_base,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(initial, separators=(',', ':'))}\n\n"
        for word in content.split(" "):
            chunk = {
                **chunk_base,
                "choices": [{"index": 0, "delta": {"content": word + " "}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
            await asyncio.sleep(0)
        final = {
            **chunk_base,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason", "stop")}
            ],
        }
        yield f"data: {json.dumps(final, separators=(',', ':'))}\n\n"
        if include_usage:
            usage = {**chunk_base, "choices": [], "usage": completion.get("usage")}
            yield f"data: {json.dumps(usage, separators=(',', ':'))}\n\n"
        yield "data: [DONE]\n\n"

    return generate()


async def _stream_response(events: Any, headers: dict[str, str]) -> StreamingResponse:
    if stream_connections.locked():
        raise EcoRouteError(
            "Too many streaming connections",
            status_code=429,
            code="sse_connection_limit",
            error_type="rate_limit_error",
        )
    await stream_connections.acquire()

    async def limited() -> Any:
        try:
            async for event in events:
                yield event
        finally:
            stream_connections.release()

    return StreamingResponse(limited(), media_type="text/event-stream", headers=headers)


async def _complete_cache_hit(
    *,
    session: AsyncSession,
    redis: Redis,
    request_row: GatewayRequest,
    completion: dict[str, Any],
    kind: str,
    logical: LogicalModel,
    workspace: Workspace,
    source_endpoint_id: uuid.UUID,
    baseline_energy: float,
    baseline_cost: Decimal,
    baseline_endpoint: ModelEndpoint,
    baseline_reading: CarbonReading,
) -> dict[str, Any]:
    fresh = freshen_cached_completion(completion)
    request_row.status = "completed"
    request_row.cache_status = kind
    request_row.output_tokens = int(fresh.get("usage", {}).get("completion_tokens", 0))
    request_row.selected_endpoint_id = source_endpoint_id
    request_row.completed_at = utcnow()
    request_row.duration_ms = int(
        (request_row.completed_at - request_row.started_at).total_seconds() * 1000
    )
    lookup_energy = settings.cache_lookup_kwh
    intensity = baseline_reading.intensity_gco2_kwh
    actual_energy = lookup_energy
    carbon_available = _carbon_accounting_available(baseline_endpoint, baseline_reading)
    session.add(
        ImpactRecord(
            request_id=request_row.id,
            strategy="end_to_end",
            baseline_energy_kwh=baseline_energy,
            actual_energy_kwh=actual_energy,
            baseline_carbon_g=baseline_energy * intensity,
            actual_carbon_g=actual_energy * intensity,
            raw_carbon_delta_g=(baseline_energy - actual_energy) * intensity,
            baseline_cost_usd=baseline_cost,
            actual_cost_usd=Decimal(0),
            carbon_accounting_available=carbon_available,
            evidence={
                "energy_level": "estimated",
                "carbon_level": baseline_reading.evidence,
                "energy_source": "configured-cache-lookup-coefficient",
                "carbon_source": baseline_reading.source,
                "coefficient_version": "cache-lookup-v1",
                "carbon_observed_at": baseline_reading.observed_at.isoformat(),
                "carbon_metadata": baseline_reading.metadata,
                "grid_zone": baseline_reading.zone,
                "endpoint_region": baseline_endpoint.region,
                "processing_location_evidence": baseline_endpoint.processing_location_evidence,
                "grid_attribution": baseline_endpoint.grid_attribution,
                "carbon_accounting_available": carbon_available,
                "attribution_method": "cache_avoidance",
                "claim_scope": _claim_scope(baseline_endpoint),
            },
        )
    )
    await publish_event(
        redis,
        settings,
        workspace.id,
        "cache.hit",
        {"requestId": str(request_row.id), "kind": kind, "logicalModel": logical.alias},
    )
    await publish_event(
        redis,
        settings,
        workspace.id,
        "route.completed",
        {"requestId": str(request_row.id), "cache": kind, "route": "cache"},
    )
    metrics.CACHE_HITS.labels(kind).inc()
    metrics.REQUESTS.labels(logical.alias, "cache", kind, "success").inc()
    metrics.REQUEST_DURATION.labels(logical.alias, "cache").observe(
        (request_row.duration_ms or 0) / 1000
    )
    metrics.ENERGY.labels("cache", "estimated").inc(settings.cache_lookup_kwh)
    if carbon_available:
        metrics.AVOIDED_CARBON.labels("cache", baseline_reading.evidence).inc(
            max(0.0, (baseline_energy - settings.cache_lookup_kwh) * intensity)
        )
    return fresh


@router.get("/v1/models", dependencies=[Depends(require_gateway_key)])
async def list_models(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    models = list(
        (
            await session.scalars(
                select(LogicalModel).where(
                    LogicalModel.enabled.is_(True), LogicalModel.deleted_at.is_(None)
                )
            )
        ).all()
    )
    return {
        "object": "list",
        "data": [
            {"id": model.alias, "object": "model", "created": 1784064000, "owned_by": "ecoroute"}
            for model in models
        ],
    }


@router.post("/v1/chat/completions", dependencies=[Depends(require_gateway_key)])
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> Response:
    started = time.monotonic()
    request_id = uuid.UUID(request.state.request_id)
    workspace, logical, policy_row, policy, endpoint_rows = await _load_context(
        session, body.model, redis
    )
    features = normalize_request(request_id, body)
    audit_features = features.model_dump(mode="json")
    # The classifier and cache use this value in-memory; the audit trail stores only
    # its separately redacted preview.
    audit_features.pop("normalized_text", None)
    audit_features["capability_passthrough"] = features.has_multimodal
    audit_features["extra_fields"] = sorted((body.model_extra or {}).keys())
    audit_features["transformed_fields"] = ["model"]
    audit_features["ignored_fields"] = ["metadata"] if body.metadata else []
    row = GatewayRequest(
        id=request_id,
        workspace_id=workspace.id,
        logical_model_id=logical.id,
        requested_model_alias=body.model,
        status="routing",
        stream=body.stream,
        input_tokens=features.input_token_estimate,
        request_features=audit_features,
        client_metadata=body.metadata or {},
        redacted_prompt_preview=features.redacted_preview,
        cache_status="miss",
        started_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    await publish_event(
        redis, settings, workspace.id, "route.started", {"requestId": str(request_id)}
    )
    cache = CacheService(redis, settings)
    fingerprint = exact_fingerprint(workspace.id, body, features, policy.namespace_version)
    preclassification = deterministic_classify(features)

    if exact_cache_eligible(features, policy, preclassification):
        hit = await cache.exact_get(workspace.id, fingerprint)
        if hit:
            baseline_endpoint = next(
                endpoint
                for endpoint in endpoint_rows
                if endpoint.id == logical.baseline_endpoint_id
            )
            scenario = await _redis_text(redis, "ecoroute:demo:grid", "moderate")
            baseline_reading = await CarbonService(settings, redis).reading(
                session,
                baseline_endpoint.grid_zone,
                demo_scenario=scenario if settings.demo_mode else None,
                allow_stale_minutes=policy.allow_stale_carbon_minutes,
                **_carbon_lookup(baseline_endpoint),
            )
            completion = await _complete_cache_hit(
                session=session,
                redis=redis,
                request_row=row,
                completion=hit["completion"],
                kind="exact",
                logical=logical,
                workspace=workspace,
                source_endpoint_id=uuid.UUID(hit["source_endpoint_id"]),
                baseline_energy=float(hit["baseline_energy_kwh"]),
                baseline_cost=Decimal(hit["baseline_cost_usd"]),
                baseline_endpoint=baseline_endpoint,
                baseline_reading=baseline_reading,
            )
            await session.commit()
            headers = _headers(
                request_id,
                "cache",
                row.selected_endpoint_id,
                "exact",
                baseline_reading.evidence,
                False,
                carbon_accounting_available=_carbon_accounting_available(
                    baseline_endpoint, baseline_reading
                ),
                grid_attribution=baseline_endpoint.grid_attribution,
                processing_region=baseline_endpoint.region,
                provider_deployment_type=baseline_endpoint.azure_deployment_type,
            )
            if body.stream:
                return await _stream_response(
                    _completion_sse(
                        completion,
                        bool(body.stream_options and body.stream_options.get("include_usage")),
                    ),
                    headers,
                )
            return JSONResponse(completion, headers=headers)

    if semantic_cache_eligible(features, policy, preclassification):
        semantic = await cache.semantic_find(
            session,
            workspace_id=workspace.id,
            logical_model_id=logical.id,
            features=features,
            namespace_version=policy.namespace_version,
            threshold=policy.semantic_similarity_threshold,
        )
        if semantic:
            baseline_endpoint = next(
                endpoint
                for endpoint in endpoint_rows
                if endpoint.id == logical.baseline_endpoint_id
            )
            scenario = await _redis_text(redis, "ecoroute:demo:grid", "moderate")
            baseline_reading = await CarbonService(settings, redis).reading(
                session,
                baseline_endpoint.grid_zone,
                demo_scenario=scenario if settings.demo_mode else None,
                allow_stale_minutes=policy.allow_stale_carbon_minutes,
                **_carbon_lookup(baseline_endpoint),
            )
            completion = await _complete_cache_hit(
                session=session,
                redis=redis,
                request_row=row,
                completion=semantic.completion,
                kind="semantic",
                logical=logical,
                workspace=workspace,
                source_endpoint_id=semantic.source_endpoint_id,
                baseline_energy=semantic.baseline_energy_kwh,
                baseline_cost=semantic.baseline_cost_usd,
                baseline_endpoint=baseline_endpoint,
                baseline_reading=baseline_reading,
            )
            await session.commit()
            headers = _headers(
                request_id,
                "cache",
                row.selected_endpoint_id,
                "semantic",
                baseline_reading.evidence,
                False,
                carbon_accounting_available=_carbon_accounting_available(
                    baseline_endpoint, baseline_reading
                ),
                grid_attribution=baseline_endpoint.grid_attribution,
                processing_region=baseline_endpoint.region,
                provider_deployment_type=baseline_endpoint.azure_deployment_type,
            )
            if body.stream:
                return await _stream_response(
                    _completion_sse(
                        completion,
                        bool(body.stream_options and body.stream_options.get("include_usage")),
                    ),
                    headers,
                )
            return JSONResponse(completion, headers=headers)

    router_started = time.monotonic()
    classification = await classify(features, settings)
    if classification.confidence < policy.min_router_confidence:
        classification = RouterClassification.fail_closed("ROUTER_BELOW_POLICY_CONFIDENCE")
    metrics.ROUTER_DURATION.observe(time.monotonic() - router_started)
    row.router_classification = classification.model_dump(mode="json")
    scenario = await _redis_text(redis, "ecoroute:demo:grid", "moderate")
    carbon_service = CarbonService(settings, redis)
    readings = {
        endpoint.id: await carbon_service.reading(
            session,
            endpoint.grid_zone,
            demo_scenario=scenario if settings.demo_mode else None,
            allow_stale_minutes=policy.allow_stale_carbon_minutes,
            **_carbon_lookup(endpoint),
        )
        for endpoint in endpoint_rows
    }
    for endpoint in endpoint_rows:
        reading = readings[endpoint.id]
        if _carbon_accounting_available(endpoint, reading):
            metrics.GRID_INTENSITY.labels(reading.zone, reading.source, reading.evidence).set(
                reading.intensity_gco2_kwh
            )
    profile_ids = {endpoint.slm_profile_id for endpoint in endpoint_rows if endpoint.slm_profile_id}
    profiles = {
        profile.id: profile
        for profile in (
            await session.scalars(select(SlmProfile).where(SlmProfile.id.in_(profile_ids)))
        ).all()
    }
    candidates = [
        _candidate(
            endpoint,
            readings[endpoint.id],
            profiles.get(endpoint.slm_profile_id) if endpoint.slm_profile_id else None,
        )
        for endpoint in endpoint_rows
    ]
    baseline = next(item for item in candidates if item.id == logical.baseline_endpoint_id)
    baseline_row = next(item for item in endpoint_rows if item.id == baseline.id)
    assert logical.required_fallback_endpoint_id is not None
    try:
        selected, snapshots, selection_reason = select_candidate(
            candidates,
            features,
            classification,
            policy,
            baseline,
            logical.required_fallback_endpoint_id,
        )
    except ValueError as exc:
        row.status = "failed"
        row.error_code = "no_eligible_endpoint"
        await session.commit()
        raise EcoRouteError(
            str(exc), status_code=422, code="no_eligible_endpoint", error_type="routing_error"
        ) from exc

    selected_row = next(endpoint for endpoint in endpoint_rows if endpoint.id == selected.id)
    row.selected_endpoint_id = selected.id
    decision = RouteDecision(
        request_id=request_id,
        policy_id=policy_row.id,
        grid_state=(
            "unknown"
            if not selected.carbon_available
            else "clean"
            if selected.grid_intensity <= policy.clean_threshold_gco2_kwh
            else "dirty"
            if selected.grid_intensity >= policy.dirty_threshold_gco2_kwh
            else "moderate"
        ),
        candidate_snapshot=[snapshot.model_dump(mode="json") for snapshot in snapshots],
        selected_endpoint_id=selected.id,
        selection_reason=selection_reason,
        score_breakdown={"weights": policy.weights.model_dump(mode="json")},
    )
    session.add(decision)
    await publish_event(
        redis,
        settings,
        workspace.id,
        "route.selected",
        {"requestId": str(request_id), "endpointId": str(selected.id), "reason": selection_reason},
    )

    requires_post_validator = selected.quality_tier == "specialized" or (
        policy.quality_fallback_enabled
        and classification.task_type in {"policy_qa", "order_support"}
    )
    live_stream = body.stream and classification.confidence >= 0.90 and not requires_post_validator
    if live_stream:
        stream_attempt = 1
        stream_fallback = False

        async def preflight_stream(endpoint: ModelEndpoint) -> tuple[Any, dict[str, Any]]:
            iterator = providers.for_provider(endpoint.provider).stream(endpoint, body).__aiter__()
            try:
                async with asyncio.timeout(settings.stream_timeout_seconds):
                    first = await anext(iterator)
                return iterator, first
            except StopAsyncIteration as exc:
                raise ProviderError(
                    "Upstream stream ended without a chunk", "upstream_invalid_response", 502
                ) from exc
            except TimeoutError as exc:
                raise ProviderError("Upstream timed out", "upstream_timeout", 504) from exc

        stream_started = time.monotonic()
        try:
            stream_iterator, first_chunk = await preflight_stream(selected_row)
        except ProviderError as first_error:
            session.add(
                ModelAttempt(
                    request_id=request_id,
                    attempt_number=1,
                    endpoint_id=selected.id,
                    purpose="selected",
                    status="failed",
                    duration_ms=int((time.monotonic() - stream_started) * 1000),
                    error_code=first_error.code,
                    completed_at=utcnow(),
                )
            )
            eligible_ids = {
                snapshot.endpoint_id for snapshot in snapshots if snapshot.excluded_reason is None
            }
            fallback_row = next(
                (
                    endpoint
                    for endpoint in endpoint_rows
                    if endpoint.id == logical.required_fallback_endpoint_id
                    and endpoint.id != selected.id
                    and endpoint.id in eligible_ids
                ),
                None,
            )
            if (
                first_error.code
                not in {
                    "rate_limit_error",
                    "upstream_timeout",
                    "upstream_transport_error",
                    "upstream_error",
                }
                or fallback_row is None
            ):
                row.status = "failed"
                row.error_code = first_error.code
                row.completed_at = utcnow()
                row.duration_ms = int((time.monotonic() - started) * 1000)
                await session.commit()
                raise EcoRouteError(
                    str(first_error),
                    status_code=first_error.status_code,
                    code=first_error.code,
                    error_type="upstream_error",
                ) from first_error
            stream_attempt = 2
            stream_fallback = True
            selected_row = fallback_row
            selected = next(item for item in candidates if item.id == fallback_row.id)
            row.selected_endpoint_id = selected.id
            row.fallback_used = True
            decision.selected_endpoint_id = selected.id
            decision.selection_reason = "transport_fallback_before_stream"
            stream_started = time.monotonic()
            try:
                stream_iterator, first_chunk = await preflight_stream(fallback_row)
            except ProviderError as second_error:
                session.add(
                    ModelAttempt(
                        request_id=request_id,
                        attempt_number=2,
                        endpoint_id=fallback_row.id,
                        purpose="transport_fallback",
                        status="failed",
                        duration_ms=int((time.monotonic() - stream_started) * 1000),
                        error_code=second_error.code,
                        completed_at=utcnow(),
                    )
                )
                row.status = "failed"
                row.error_code = second_error.code
                row.completed_at = utcnow()
                row.duration_ms = int((time.monotonic() - started) * 1000)
                await session.commit()
                raise EcoRouteError(
                    str(second_error),
                    status_code=second_error.status_code,
                    code=second_error.code,
                    error_type="upstream_error",
                ) from second_error

        row.first_token_at = utcnow()
        metrics.TIME_TO_FIRST_TOKEN.labels(body.model, selected_row.name).observe(
            time.monotonic() - started
        )
        session.add(
            ModelAttempt(
                request_id=request_id,
                attempt_number=stream_attempt,
                endpoint_id=selected.id,
                purpose="selected" if stream_attempt == 1 else "transport_fallback",
                status="streaming",
                input_tokens=features.input_token_estimate,
            )
        )
        await session.commit()
        include_usage = bool(body.stream_options and body.stream_options.get("include_usage"))
        stream_selected = selected
        stream_selected_row = selected_row

        async def update_stream_failure(status: str, error_code: str) -> None:
            async with SessionLocal() as live_session:
                request_row = await live_session.get(GatewayRequest, request_id)
                attempt_row = await live_session.scalar(
                    select(ModelAttempt).where(
                        ModelAttempt.request_id == request_id,
                        ModelAttempt.attempt_number == stream_attempt,
                    )
                )
                if request_row is not None:
                    request_row.status = status
                    request_row.error_code = error_code
                    request_row.completed_at = utcnow()
                    request_row.duration_ms = int((time.monotonic() - started) * 1000)
                if attempt_row is not None:
                    attempt_row.status = status
                    attempt_row.error_code = error_code
                    attempt_row.completed_at = utcnow()
                    attempt_row.duration_ms = int((time.monotonic() - stream_started) * 1000)
                await live_session.commit()
            await publish_event(
                redis,
                settings,
                workspace.id,
                "route.failed",
                {"requestId": str(request_id), "errorCode": error_code},
            )

        async def live_events() -> Any:
            chunks = [first_chunk]
            content_parts: list[str] = []
            finish_reason = "stop"
            upstream_usage: dict[str, Any] | None = None
            completion_id = str(first_chunk.get("id", f"chatcmpl-{uuid.uuid4().hex}"))
            created = int(first_chunk.get("created", time.time()))
            try:
                index = 0
                while True:
                    if await request.is_disconnected():
                        await stream_iterator.aclose()
                        await update_stream_failure("client_cancelled", "client_cancelled")
                        return
                    chunk = chunks[index] if index < len(chunks) else None
                    if chunk is None:
                        try:
                            async with asyncio.timeout(settings.stream_timeout_seconds):
                                chunk = await anext(stream_iterator)
                        except StopAsyncIteration:
                            break
                    if chunk is None:
                        continue
                    index += 1
                    normalized = dict(chunk)
                    normalized["model"] = body.model
                    if normalized.get("usage"):
                        upstream_usage = normalized["usage"]
                    choices = normalized.get("choices") or []
                    for choice in choices:
                        delta = choice.get("delta") or {}
                        if isinstance(delta.get("content"), str):
                            content_parts.append(delta["content"])
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                    yield f"data: {json.dumps(normalized, separators=(',', ':'))}\n\n"
            except asyncio.CancelledError:
                await stream_iterator.aclose()
                await update_stream_failure("client_cancelled", "client_cancelled")
                return
            except ProviderError:
                await update_stream_failure("partial_stream_error", "partial_stream_error")
                return
            except Exception:
                await update_stream_failure("partial_stream_error", "partial_stream_error")
                return

            content = "".join(content_parts)
            usage = upstream_usage or {
                "prompt_tokens": features.input_token_estimate,
                "completion_tokens": max(1, len(content) // 4),
                "total_tokens": features.input_token_estimate + max(1, len(content) // 4),
            }
            input_tokens = int(usage.get("prompt_tokens", features.input_token_estimate))
            output_tokens = int(usage.get("completion_tokens", max(1, len(content) // 4)))
            impact = calculate_impact(
                baseline,
                stream_selected,
                input_tokens,
                output_tokens,
                router_energy_kwh=0.000002,
                baseline_carbon_available=baseline.carbon_available,
                selected_carbon_available=stream_selected.carbon_available,
            )
            completion = {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": body.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage,
            }
            async with SessionLocal() as live_session:
                request_row = await live_session.get(GatewayRequest, request_id)
                attempt_row = await live_session.scalar(
                    select(ModelAttempt).where(
                        ModelAttempt.request_id == request_id,
                        ModelAttempt.attempt_number == stream_attempt,
                    )
                )
                if request_row is not None:
                    request_row.status = "completed"
                    request_row.output_tokens = output_tokens
                    request_row.completed_at = utcnow()
                    request_row.duration_ms = int((time.monotonic() - started) * 1000)
                if attempt_row is not None:
                    attempt_row.status = "completed"
                    attempt_row.output_tokens = output_tokens
                    attempt_row.duration_ms = int((time.monotonic() - stream_started) * 1000)
                    attempt_row.completed_at = utcnow()
                    attempt_row.quality_verdict = {
                        "passed": True,
                        "reason": "stream_preflight_confident",
                        "confidence": classification.confidence,
                    }
                live_session.add(
                    ImpactRecord(
                        request_id=request_id,
                        strategy="end_to_end",
                        baseline_energy_kwh=impact.baseline_energy_kwh,
                        actual_energy_kwh=impact.actual_energy_kwh,
                        baseline_carbon_g=impact.baseline_carbon_g,
                        actual_carbon_g=impact.actual_carbon_g,
                        raw_carbon_delta_g=impact.raw_carbon_delta_g,
                        baseline_cost_usd=impact.baseline_cost_usd,
                        actual_cost_usd=impact.actual_cost_usd,
                        carbon_accounting_available=impact.carbon_accounting_available,
                        evidence=_impact_evidence(
                            baseline,
                            baseline_row,
                            readings[baseline.id],
                            stream_selected,
                            stream_selected_row,
                            readings[stream_selected.id],
                        ),
                    )
                )
                if exact_cache_eligible(features, policy, classification):
                    await CacheService(redis, settings).store_entry(
                        live_session,
                        workspace_id=workspace.id,
                        logical_model_id=logical.id,
                        source_request_id=request_id,
                        source_endpoint_id=stream_selected.id,
                        fingerprint=fingerprint,
                        namespace_version=policy.namespace_version,
                        features=features,
                        completion=completion,
                        quality_verdict={
                            "passed": True,
                            "reason": "stream_preflight_confident",
                            "task_type": classification.task_type,
                        },
                        baseline_energy_kwh=impact.baseline_energy_kwh,
                        baseline_cost_usd=impact.baseline_cost_usd,
                        ttl_seconds=max(
                            60,
                            int(
                                policy.cache_ttl_seconds
                                * (
                                    0.5
                                    if decision.grid_state == "clean"
                                    else 2.0
                                    if decision.grid_state == "dirty"
                                    else 1.0
                                )
                            ),
                        ),
                        task_type=classification.task_type,
                    )
                await live_session.commit()
            await publish_event(
                redis,
                settings,
                workspace.id,
                "route.completed",
                {
                    "requestId": str(request_id),
                    "route": stream_selected_row.name,
                    "cache": "miss",
                    "fallback": stream_fallback,
                    "carbonGrams": (
                        impact.actual_carbon_g if impact.carbon_accounting_available else None
                    ),
                },
            )
            metrics.REQUESTS.labels(body.model, stream_selected_row.name, "miss", "success").inc()
            metrics.REQUEST_DURATION.labels(body.model, stream_selected_row.name).observe(
                time.monotonic() - started
            )
            metrics.TOKENS.labels("input", stream_selected_row.name).inc(input_tokens)
            metrics.TOKENS.labels("output", stream_selected_row.name).inc(output_tokens)
            metrics.COST.labels(stream_selected_row.name).inc(float(impact.actual_cost_usd))
            metrics.ENERGY.labels(stream_selected_row.name, stream_selected.energy_evidence).inc(
                impact.actual_energy_kwh
            )
            if impact.carbon_accounting_available:
                metrics.CARBON.labels(
                    stream_selected_row.name, readings[stream_selected.id].evidence
                ).inc(impact.actual_carbon_g)
            if include_usage and upstream_usage is None:
                usage_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": body.model,
                    "choices": [],
                    "usage": usage,
                }
                yield f"data: {json.dumps(usage_chunk, separators=(',', ':'))}\n\n"
            yield "data: [DONE]\n\n"

        route_name = (
            "frontier" if stream_selected.quality_tier == "frontier" else stream_selected_row.name
        )
        return await _stream_response(
            live_events(),
            _headers(
                request_id,
                route_name,
                stream_selected.id,
                "miss",
                readings[stream_selected.id].evidence,
                stream_fallback,
                carbon_accounting_available=(
                    baseline.carbon_available and stream_selected.carbon_available
                ),
                grid_attribution=stream_selected_row.grid_attribution,
                processing_region=stream_selected_row.region,
                provider_deployment_type=stream_selected_row.azure_deployment_type,
            ),
        )

    fallback_used = False
    force_failure = (await redis.get("ecoroute:demo:quality-failure")) == "1"
    if force_failure:
        await redis.delete("ecoroute:demo:quality-failure")
    attempt_number = 1
    attempt_start = time.monotonic()

    async def invoke(endpoint: ModelEndpoint) -> dict[str, Any]:
        remaining = settings.provider_timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise ProviderError("Request deadline exceeded", "upstream_timeout", 504)
        try:
            async with asyncio.timeout(remaining):
                return await providers.for_provider(endpoint.provider).chat(endpoint, body)
        except TimeoutError as exc:
            raise ProviderError("Upstream timed out", "upstream_timeout", 504) from exc

    try:
        completion = await invoke(selected_row)
    except ProviderError as first_error:
        session.add(
            ModelAttempt(
                request_id=request_id,
                attempt_number=1,
                endpoint_id=selected.id,
                purpose="selected",
                status="failed",
                duration_ms=int((time.monotonic() - attempt_start) * 1000),
                error_code=first_error.code,
                completed_at=utcnow(),
            )
        )
        retryable = first_error.code in {
            "rate_limit_error",
            "upstream_timeout",
            "upstream_transport_error",
            "upstream_error",
        }
        eligible_ids = {
            snapshot.endpoint_id for snapshot in snapshots if snapshot.excluded_reason is None
        }
        retry_row = next(
            (
                endpoint
                for endpoint in endpoint_rows
                if endpoint.id == logical.required_fallback_endpoint_id
                and endpoint.id != selected.id
                and endpoint.id in eligible_ids
            ),
            None,
        )
        if not retryable or retry_row is None:
            row.status = "failed"
            row.error_code = first_error.code
            row.completed_at = utcnow()
            row.duration_ms = int((time.monotonic() - started) * 1000)
            await publish_event(
                redis,
                settings,
                workspace.id,
                "route.failed",
                {"requestId": str(request_id), "errorCode": first_error.code},
            )
            await session.commit()
            error_type = (
                "rate_limit_error"
                if first_error.status_code == 429
                else "upstream_timeout"
                if first_error.status_code == 504
                else "upstream_configuration_error"
                if first_error.code.startswith("upstream_auth")
                else "upstream_error"
            )
            raise EcoRouteError(
                str(first_error),
                status_code=first_error.status_code,
                code=first_error.code,
                error_type=error_type,
            ) from first_error
        fallback_used = True
        row.fallback_used = True
        attempt_number = 2
        retry_start = time.monotonic()
        selected_row = retry_row
        selected = next(item for item in candidates if item.id == retry_row.id)
        row.selected_endpoint_id = selected.id
        try:
            completion = await invoke(retry_row)
        except ProviderError as second_error:
            session.add(
                ModelAttempt(
                    request_id=request_id,
                    attempt_number=2,
                    endpoint_id=retry_row.id,
                    purpose="transport_fallback",
                    status="failed",
                    duration_ms=int((time.monotonic() - retry_start) * 1000),
                    error_code=second_error.code,
                    completed_at=utcnow(),
                )
            )
            row.status = "failed"
            row.error_code = second_error.code
            row.completed_at = utcnow()
            row.duration_ms = int((time.monotonic() - started) * 1000)
            await publish_event(
                redis,
                settings,
                workspace.id,
                "route.failed",
                {"requestId": str(request_id), "errorCode": second_error.code},
            )
            await session.commit()
            raise EcoRouteError(
                str(second_error),
                status_code=second_error.status_code,
                code=second_error.code,
                error_type="upstream_error",
            ) from second_error
        attempt_start = retry_start
    if not isinstance(completion.get("choices"), list) or not completion["choices"]:
        session.add(
            ModelAttempt(
                request_id=request_id,
                attempt_number=attempt_number,
                endpoint_id=selected.id,
                purpose="selected" if attempt_number == 1 else "transport_fallback",
                status="failed",
                duration_ms=int((time.monotonic() - attempt_start) * 1000),
                error_code="upstream_invalid_response",
                completed_at=utcnow(),
            )
        )
        row.status = "failed"
        row.error_code = "upstream_invalid_response"
        row.completed_at = utcnow()
        row.duration_ms = int((time.monotonic() - started) * 1000)
        await session.commit()
        raise EcoRouteError(
            "Upstream returned an invalid Chat Completion",
            status_code=502,
            code="upstream_invalid_response",
            error_type="upstream_error",
        )
    completion["model"] = body.model
    attempt = ModelAttempt(
        request_id=request_id,
        attempt_number=attempt_number,
        endpoint_id=selected.id,
        purpose="selected" if attempt_number == 1 else "transport_fallback",
        status="completed",
        input_tokens=int(
            completion.get("usage", {}).get("prompt_tokens", features.input_token_estimate)
        ),
        output_tokens=int(completion.get("usage", {}).get("completion_tokens", 0)),
        duration_ms=int((time.monotonic() - attempt_start) * 1000),
        completed_at=utcnow(),
    )
    selected_message = completion["choices"][0]["message"]
    raw = str(selected_message.get("content") or "")
    if selected_message.get("tool_calls") and body.tools and "tools" in selected.capabilities:
        verdict = QualityVerdict(passed=True, reason="allowed_tool_call")
    else:
        verdict = verify_output(
            raw,
            classification,
            specialized=selected.quality_tier == "specialized",
            force_failure=force_failure,
            response_format=body.response_format,
            minimum_support_confidence=policy.min_slm_confidence,
        )
    attempt.quality_verdict = verdict.model_dump(mode="json")
    session.add(attempt)

    if not verdict.passed and policy.quality_fallback_enabled and attempt_number < 2:
        eligible_ids = {
            snapshot.endpoint_id for snapshot in snapshots if snapshot.excluded_reason is None
        }
        fallback_candidates = [
            endpoint
            for endpoint in endpoint_rows
            if endpoint.id != selected.id
            and endpoint.quality_tier == "frontier"
            and endpoint.enabled
            and endpoint.id in eligible_ids
            and endpoint.id == logical.required_fallback_endpoint_id
        ]
        fallback_row = fallback_candidates[0] if fallback_candidates else None
        if fallback_row is not None:
            fallback_used = True
            fallback_start = time.monotonic()
            try:
                completion = await invoke(fallback_row)
            except ProviderError as fallback_error:
                session.add(
                    ModelAttempt(
                        request_id=request_id,
                        attempt_number=2,
                        endpoint_id=fallback_row.id,
                        purpose="quality_fallback",
                        status="failed",
                        duration_ms=int((time.monotonic() - fallback_start) * 1000),
                        error_code=fallback_error.code,
                        completed_at=utcnow(),
                    )
                )
                row.status = "failed"
                row.error_code = fallback_error.code
                row.completed_at = utcnow()
                row.duration_ms = int((time.monotonic() - started) * 1000)
                await publish_event(
                    redis,
                    settings,
                    workspace.id,
                    "route.failed",
                    {"requestId": str(request_id), "errorCode": fallback_error.code},
                )
                await session.commit()
                raise EcoRouteError(
                    "Frontier quality fallback failed",
                    status_code=fallback_error.status_code,
                    code=fallback_error.code,
                    error_type="upstream_error",
                ) from fallback_error
            fallback_choices = completion.get("choices")
            if not isinstance(fallback_choices, list) or not fallback_choices:
                row.status = "failed"
                row.error_code = "upstream_invalid_response"
                row.completed_at = utcnow()
                await session.commit()
                raise EcoRouteError(
                    "Frontier fallback returned an invalid Chat Completion",
                    status_code=502,
                    code="upstream_invalid_response",
                    error_type="upstream_error",
                )
            completion["model"] = body.model
            fallback_candidate = next(item for item in candidates if item.id == fallback_row.id)
            fallback_message = fallback_choices[0].get("message", {})
            fallback_raw = str(fallback_message.get("content") or "")
            if (
                fallback_message.get("tool_calls")
                and body.tools
                and "tools" in fallback_candidate.capabilities
            ):
                fallback_verdict = QualityVerdict(passed=True, reason="allowed_tool_call")
            else:
                fallback_verdict = verify_output(
                    fallback_raw,
                    classification,
                    specialized=False,
                    response_format=body.response_format,
                )
            selected, selected_row = fallback_candidate, fallback_row
            session.add(
                ModelAttempt(
                    request_id=request_id,
                    attempt_number=2,
                    endpoint_id=selected.id,
                    purpose="quality_fallback",
                    status="completed",
                    input_tokens=int(
                        completion.get("usage", {}).get(
                            "prompt_tokens", features.input_token_estimate
                        )
                    ),
                    output_tokens=int(completion.get("usage", {}).get("completion_tokens", 0)),
                    duration_ms=int((time.monotonic() - fallback_start) * 1000),
                    quality_verdict=fallback_verdict.model_dump(mode="json"),
                    completed_at=utcnow(),
                )
            )
            row.selected_endpoint_id = selected.id
            row.fallback_used = True
            decision.selected_endpoint_id = selected.id
            decision.selection_reason = "quality_fallback"
            metrics.QUALITY_FALLBACKS.labels(verdict.reason).inc()
            verdict = fallback_verdict
    if not verdict.passed:
        row.status = "failed"
        row.error_code = "quality_validation_failed"
        row.completed_at = utcnow()
        row.duration_ms = int((time.monotonic() - started) * 1000)
        await publish_event(
            redis,
            settings,
            workspace.id,
            "route.failed",
            {"requestId": str(request_id), "errorCode": row.error_code},
        )
        await session.commit()
        raise EcoRouteError(
            "The selected response failed required quality validation",
            status_code=502,
            code="quality_validation_failed",
            error_type="upstream_error",
        )
    if verdict.answer is not None and selected.quality_tier == "specialized":
        completion["choices"][0]["message"]["content"] = verdict.answer

    usage = completion.get("usage", {})
    input_tokens = int(usage.get("prompt_tokens", features.input_token_estimate))
    output_tokens = int(usage.get("completion_tokens", max(1, len(str(completion)) // 4)))
    impact = calculate_impact(
        baseline,
        selected,
        input_tokens,
        output_tokens,
        router_energy_kwh=0.000002,
        baseline_carbon_available=baseline.carbon_available,
        selected_carbon_available=selected.carbon_available,
    )
    row.status = "completed"
    row.output_tokens = output_tokens
    row.completed_at = utcnow()
    row.duration_ms = int((time.monotonic() - started) * 1000)
    session.add(
        ImpactRecord(
            request_id=request_id,
            strategy="end_to_end",
            baseline_energy_kwh=impact.baseline_energy_kwh,
            actual_energy_kwh=impact.actual_energy_kwh,
            baseline_carbon_g=impact.baseline_carbon_g,
            actual_carbon_g=impact.actual_carbon_g,
            raw_carbon_delta_g=impact.raw_carbon_delta_g,
            baseline_cost_usd=impact.baseline_cost_usd,
            actual_cost_usd=impact.actual_cost_usd,
            carbon_accounting_available=impact.carbon_accounting_available,
            evidence=_impact_evidence(
                baseline,
                baseline_row,
                readings[baseline.id],
                selected,
                selected_row,
                readings[selected.id],
            ),
        )
    )
    if exact_cache_eligible(features, policy, classification) and verdict.passed:
        await cache.store_entry(
            session,
            workspace_id=workspace.id,
            logical_model_id=logical.id,
            source_request_id=request_id,
            source_endpoint_id=selected.id,
            fingerprint=fingerprint,
            namespace_version=policy.namespace_version,
            features=features,
            completion=completion,
            quality_verdict={
                **verdict.model_dump(mode="json"),
                "task_type": classification.task_type,
            },
            baseline_energy_kwh=impact.baseline_energy_kwh,
            baseline_cost_usd=impact.baseline_cost_usd,
            ttl_seconds=max(
                60,
                int(
                    policy.cache_ttl_seconds
                    * (
                        0.5
                        if decision.grid_state == "clean"
                        else 2.0
                        if decision.grid_state == "dirty"
                        else 1.0
                    )
                ),
            ),
            task_type=classification.task_type,
        )
        await publish_event(
            redis, settings, workspace.id, "cache.stored", {"requestId": str(request_id)}
        )
    await publish_event(
        redis,
        settings,
        workspace.id,
        "route.completed",
        {
            "requestId": str(request_id),
            "route": selected_row.name,
            "cache": "miss",
            "fallback": fallback_used,
            "carbonGrams": impact.actual_carbon_g if impact.carbon_accounting_available else None,
        },
    )
    await session.commit()
    metrics.REQUESTS.labels(body.model, selected_row.name, "miss", "success").inc()
    metrics.REQUEST_DURATION.labels(body.model, selected_row.name).observe(
        time.monotonic() - started
    )
    metrics.TOKENS.labels("input", selected_row.name).inc(input_tokens)
    metrics.TOKENS.labels("output", selected_row.name).inc(output_tokens)
    metrics.COST.labels(selected_row.name).inc(float(impact.actual_cost_usd))
    metrics.ENERGY.labels(selected_row.name, selected.energy_evidence).inc(impact.actual_energy_kwh)
    if impact.carbon_accounting_available:
        metrics.CARBON.labels(selected_row.name, readings[selected.id].evidence).inc(
            impact.actual_carbon_g
        )
        metrics.AVOIDED_CARBON.labels("end_to_end", readings[selected.id].evidence).inc(
            impact.avoided_carbon_g
        )
    route_name = "frontier" if selected.quality_tier == "frontier" else selected_row.name
    headers = _headers(
        request_id,
        route_name,
        selected.id,
        "miss",
        readings[selected.id].evidence,
        fallback_used,
        carbon_accounting_available=impact.carbon_accounting_available,
        grid_attribution=selected_row.grid_attribution,
        processing_region=selected_row.region,
        provider_deployment_type=selected_row.azure_deployment_type,
    )
    if body.metadata and body.metadata.get("ecoroute_debug") == "true" and settings.demo_mode:
        completion["ecoroute"] = {
            "request_id": str(request_id),
            "selection_reason": selection_reason,
            "fallback_used": fallback_used,
        }
    if body.stream:
        return StreamingResponse(
            _completion_sse(
                completion, bool(body.stream_options and body.stream_options.get("include_usage"))
            ),
            media_type="text/event-stream",
            headers=headers,
        )
    return JSONResponse(completion, headers=headers)
