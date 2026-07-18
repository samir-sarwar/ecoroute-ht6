from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import APIRouter, Body, Depends, Header, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ecoroute.api.auth import require_agent_token, require_gateway_key
from ecoroute.api.errors import EcoRouteError
from ecoroute.api.events import publish_event
from ecoroute.api.gateway import _candidate
from ecoroute.api.schemas import (
    AgentRegistration,
    ChatCompletionRequest,
    ChatMessage,
    ModelEndpointCreate,
    RouterClassification,
    RoutingPolicyConfig,
    TelemetryPayload,
)
from ecoroute.cache.embeddings import cosine_similarity, get_local_embedder
from ecoroute.carbon.providers import FixtureCarbonProvider
from ecoroute.config import get_settings
from ecoroute.db.base import utcnow, uuid7
from ecoroute.db.models import (
    Benchmark,
    CacheEntry,
    CarbonReadingRecord,
    Dataset,
    DatasetExample,
    GatewayRequest,
    ImpactRecord,
    Job,
    LogicalModel,
    LogicalModelEndpoint,
    ModelAttempt,
    ModelEndpoint,
    NodeAgent,
    OptimizationEvent,
    PolicyDocument,
    RouteDecision,
    RoutingPolicy,
    SlmProfile,
    TelemetrySample,
    TrainingRun,
    TrainingRunEvent,
    Workspace,
)
from ecoroute.db.session import get_redis, get_session
from ecoroute.providers.registry import ProviderRegistry
from ecoroute.routing.classifier import deterministic_classify
from ecoroute.routing.engine import select_candidate
from ecoroute.routing.safety import normalize_request, redact
from ecoroute.telemetry import metrics

settings = get_settings()
providers = ProviderRegistry(settings)
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_gateway_key)])
agent_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_agent_token)])
event_connections = asyncio.Semaphore(settings.max_sse_connections)
PROJECT_ROOT = Path(__file__).resolve().parents[4]


async def _redis_text(redis: Redis, key: str, default: str) -> str:
    value = await redis.get(key)
    if isinstance(value, bytes):
        return value.decode()
    return value or default


async def _workspace(session: AsyncSession) -> Workspace:
    workspace = await session.scalar(select(Workspace).limit(1))
    if workspace is None:
        raise EcoRouteError(
            "Demo workspace is not seeded",
            status_code=503,
            code="workspace_unavailable",
            error_type="service_unavailable",
        )
    return workspace


def _uuid_field(value: Any, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise EcoRouteError(f"{name} must be a UUID", code="invalid_identifier") from exc


def _uuid_cursor(value: str | None, resource: str) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise EcoRouteError(f"Invalid {resource} cursor", code="invalid_cursor") from exc


def _uuid_page(items: list[Any], limit: int) -> tuple[list[Any], str | None]:
    has_more = len(items) > limit
    visible = items[:limit]
    return visible, str(visible[-1].id) if has_more and visible else None


async def _validate_logical_references(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    endpoint_ids: list[uuid.UUID],
    baseline_id: uuid.UUID,
    fallback_id: uuid.UUID,
    policy_id: uuid.UUID,
) -> tuple[list[ModelEndpoint], RoutingPolicy]:
    if not endpoint_ids or len(endpoint_ids) != len(set(endpoint_ids)):
        raise EcoRouteError(
            "The endpoint pool must contain at least one unique endpoint",
            code="invalid_pool",
        )
    endpoints = list(
        (
            await session.scalars(
                select(ModelEndpoint).where(
                    ModelEndpoint.id.in_(endpoint_ids),
                    ModelEndpoint.workspace_id == workspace_id,
                    ModelEndpoint.deleted_at.is_(None),
                )
            )
        ).all()
    )
    if {item.id for item in endpoints} != set(endpoint_ids):
        raise EcoRouteError(
            "Endpoint pool contains an unknown or deleted endpoint",
            code="endpoint_not_found",
        )
    if baseline_id not in set(endpoint_ids) or fallback_id not in set(endpoint_ids):
        raise EcoRouteError(
            "Baseline and fallback must belong to the endpoint pool", code="invalid_pool"
        )
    fallback = next(item for item in endpoints if item.id == fallback_id)
    if fallback.quality_tier != "frontier" or not fallback.enabled:
        raise EcoRouteError(
            "The required fallback must be an enabled frontier endpoint",
            code="invalid_fallback",
        )
    policy = await session.get(RoutingPolicy, policy_id)
    if policy is None or policy.workspace_id != workspace_id:
        raise EcoRouteError("Routing policy not found", status_code=404, code="not_found")
    config = RoutingPolicyConfig.model_validate(policy.config)
    if config.enabled_endpoint_ids and (
        baseline_id not in config.enabled_endpoint_ids
        or fallback_id not in config.enabled_endpoint_ids
    ):
        raise EcoRouteError(
            "The policy allowlist must include the baseline and required fallback",
            code="invalid_policy_pool",
        )
    return endpoints, policy


def _body_fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


async def _enqueue_job(
    session: AsyncSession,
    redis: Redis,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> Job:
    if not 1 <= len(idempotency_key) <= 200:
        raise EcoRouteError(
            "Idempotency-Key must be 1-200 characters", code="invalid_idempotency_key"
        )
    enriched = {**payload, "request_fingerprint": _body_fingerprint(payload)}
    existing = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        if (
            existing.kind != kind
            or existing.input.get("request_fingerprint") != enriched["request_fingerprint"]
        ):
            raise EcoRouteError(
                "Idempotency-Key was already used with a different request",
                status_code=409,
                code="idempotency_conflict",
            )
        return existing
    job = Job(
        workspace_id=workspace_id,
        kind=kind,
        status="queued",
        idempotency_key=idempotency_key,
        input=enriched,
    )
    session.add(job)
    await session.flush()
    await session.commit()
    await redis.xadd("ecoroute:jobs", {"job_id": str(job.id)})
    return job


def _logical_model_json(item: LogicalModel, endpoint_ids: list[uuid.UUID]) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "alias": item.alias,
        "displayName": item.display_name,
        "baselineEndpointId": str(item.baseline_endpoint_id) if item.baseline_endpoint_id else None,
        "requiredFallbackEndpointId": str(item.required_fallback_endpoint_id)
        if item.required_fallback_endpoint_id
        else None,
        "activePolicyId": str(item.active_policy_id) if item.active_policy_id else None,
        "endpointIds": [str(value) for value in endpoint_ids],
        "enabled": item.enabled,
        "version": item.version,
    }


def _policy_json(item: RoutingPolicy) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "familyId": str(item.family_id),
        "versionNumber": item.version_number,
        "name": item.name,
        "preset": item.preset,
        "config": RoutingPolicyConfig.model_validate(item.config).model_dump(
            mode="json", by_alias=True
        ),
        "createdBy": item.created_by,
        "createdAt": item.created_at.isoformat(),
    }


def _profile_json(
    item: SlmProfile, documents: list[PolicyDocument] | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": str(item.id),
        "name": item.name,
        "description": item.description,
        "businessName": item.business_name,
        "definition": item.definition,
        "contentVersion": item.content_version,
        "activeModelEndpointId": str(item.active_model_endpoint_id)
        if item.active_model_endpoint_id
        else None,
        "status": item.status,
        "version": item.version,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }
    if documents is not None:
        result["policyDocuments"] = [
            {
                "id": str(document.id),
                "policyKey": document.policy_key,
                "title": document.title,
                "content": document.content,
                "version": document.version,
                "sha256": document.content_sha256,
            }
            for document in documents
            if document.active
        ]
    return result


def _training_json(item: TrainingRun) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "datasetId": str(item.dataset_id),
        "slmProfileId": str(item.slm_profile_id) if item.slm_profile_id else None,
        "kind": item.kind,
        "algorithm": item.algorithm,
        "baseModel": item.base_model,
        "status": item.status,
        "freesoloEnvironmentId": item.freesolo_environment_id,
        "freesoloRunId": item.freesolo_run_id,
        "renderedConfig": item.rendered_config,
        "costQuoteUsd": str(item.cost_quote_usd) if item.cost_quote_usd is not None else None,
        "evalMetrics": item.eval_metrics,
        "deploymentBaseUrl": item.deployment_base_url,
        "deployedModelId": item.deployed_model_id,
        "errorCode": item.error_code,
        "errorMessage": item.error_message,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
        "completedAt": item.completed_at.isoformat() if item.completed_at else None,
    }


def _approved_agent_controls(agent: NodeAgent) -> list[str]:
    configured = {
        value.strip() for value in settings.agent_approved_controls.split(",") if value.strip()
    }
    capability = {
        "gateway_concurrency": True,
        "cgroups_v2": bool(agent.capabilities.get("cgroups_v2")),
        "nice_ionice": bool(agent.capabilities.get("nice_ionice")),
        "nvml_power_limit": bool(agent.capabilities.get("nvml_power_limit")),
        "sched_ext": bool(agent.capabilities.get("sched_ext")),
        "napi_netdev_genl": bool(agent.capabilities.get("napi_netdev_genl")),
    }
    return sorted(name for name in configured if capability.get(name, False))


async def _append_training_event(
    session: AsyncSession, run: TrainingRun, event_type: str, payload: dict[str, Any]
) -> None:
    sequence = (
        int(
            await session.scalar(
                select(func.max(TrainingRunEvent.sequence)).where(
                    TrainingRunEvent.training_run_id == run.id
                )
            )
            or 0
        )
        + 1
    )
    session.add(
        TrainingRunEvent(
            training_run_id=run.id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
        )
    )


def _endpoint_json(endpoint: ModelEndpoint) -> dict[str, Any]:
    return {
        "id": str(endpoint.id),
        "name": endpoint.name,
        "provider": endpoint.provider,
        "baseUrl": endpoint.base_url,
        "credentialRef": endpoint.credential_ref,
        "physicalModel": endpoint.physical_model,
        "region": endpoint.region,
        "gridZone": endpoint.grid_zone,
        "qualityTier": endpoint.quality_tier,
        "capabilities": endpoint.capabilities,
        "contextWindowTokens": endpoint.context_window_tokens,
        "inputUsdPerMillionTokens": str(endpoint.input_usd_per_million_tokens),
        "outputUsdPerMillionTokens": str(endpoint.output_usd_per_million_tokens),
        "fixedRequestKwh": endpoint.fixed_request_kwh,
        "inputKwhPer1kTokens": endpoint.input_kwh_per_1k_tokens,
        "outputKwhPer1kTokens": endpoint.output_kwh_per_1k_tokens,
        "energyEvidence": endpoint.energy_evidence,
        "latencyP50Ms": endpoint.latency_p50_ms,
        "latencyP95Ms": endpoint.latency_p95_ms,
        "selfHosted": endpoint.self_hosted,
        "slmProfileId": str(endpoint.slm_profile_id) if endpoint.slm_profile_id else None,
        "enabled": endpoint.enabled,
        "healthState": endpoint.health_state,
        "coefficientVersion": endpoint.coefficient_version,
        "baselineConcurrency": endpoint.baseline_concurrency,
        "concurrencyTarget": endpoint.concurrency_target,
        "createdAt": endpoint.created_at.isoformat(),
        "updatedAt": endpoint.updated_at.isoformat(),
    }


@router.get("/overview")
async def overview(
    window: str = "1h",
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    workspace = await _workspace(session)
    hours = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}.get(window, 1)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    total = int(
        await session.scalar(
            select(func.count())
            .select_from(GatewayRequest)
            .where(GatewayRequest.started_at >= since)
        )
        or 0
    )
    successes = int(
        await session.scalar(
            select(func.count())
            .select_from(GatewayRequest)
            .where(GatewayRequest.started_at >= since, GatewayRequest.status == "completed")
        )
        or 0
    )
    cache_hits = int(
        await session.scalar(
            select(func.count())
            .select_from(GatewayRequest)
            .where(
                GatewayRequest.started_at >= since,
                GatewayRequest.cache_status.in_(["exact", "semantic"]),
            )
        )
        or 0
    )
    impact = (
        await session.execute(
            select(
                func.coalesce(func.sum(ImpactRecord.actual_carbon_g), 0),
                func.coalesce(func.sum(ImpactRecord.raw_carbon_delta_g), 0),
                func.coalesce(func.sum(ImpactRecord.actual_cost_usd), 0),
                func.coalesce(func.sum(ImpactRecord.baseline_cost_usd), 0),
            ).where(ImpactRecord.created_at >= since)
        )
    ).one()
    routes = list(
        (
            await session.execute(
                select(ModelEndpoint.name, func.count())
                .join(GatewayRequest, GatewayRequest.selected_endpoint_id == ModelEndpoint.id)
                .where(GatewayRequest.started_at >= since)
                .group_by(ModelEndpoint.name)
            )
        ).all()
    )
    scenario = await _redis_text(redis, "ecoroute:demo:grid", "moderate")
    reading = await FixtureCarbonProvider(scenario).reading("demo-local")
    agents = int(
        await session.scalar(
            select(func.count()).select_from(NodeAgent).where(NodeAgent.status == "online")
        )
        or 0
    )
    latest = list(
        (
            await session.scalars(
                select(GatewayRequest).order_by(GatewayRequest.started_at.desc()).limit(12)
            )
        ).all()
    )
    impact_points = list(
        (
            await session.execute(
                select(
                    GatewayRequest.started_at,
                    ImpactRecord.baseline_carbon_g,
                    ImpactRecord.actual_carbon_g,
                )
                .join(ImpactRecord, ImpactRecord.request_id == GatewayRequest.id)
                .where(GatewayRequest.started_at >= since)
                .order_by(GatewayRequest.started_at)
                .limit(500)
            )
        ).all()
    )
    endpoint_energy = list(
        (
            await session.execute(
                select(
                    ModelEndpoint.name, func.coalesce(func.sum(ImpactRecord.actual_energy_kwh), 0)
                )
                .join(GatewayRequest, GatewayRequest.selected_endpoint_id == ModelEndpoint.id)
                .join(ImpactRecord, ImpactRecord.request_id == GatewayRequest.id)
                .where(GatewayRequest.started_at >= since)
                .group_by(ModelEndpoint.name)
            )
        ).all()
    )
    quality_fallback_count = int(
        await session.scalar(
            select(func.count(ModelAttempt.id))
            .join(GatewayRequest, GatewayRequest.id == ModelAttempt.request_id)
            .where(
                GatewayRequest.started_at >= since,
                ModelAttempt.purpose == "quality_fallback",
            )
        )
        or 0
    )
    endpoint_warnings = list(
        (
            await session.scalars(
                select(ModelEndpoint).where(
                    ModelEndpoint.deleted_at.is_(None),
                    ModelEndpoint.health_state.in_(["degraded", "unhealthy", "unknown"]),
                )
            )
        ).all()
    )
    offline_agents = int(
        await session.scalar(select(func.count(NodeAgent.id)).where(NodeAgent.status == "offline"))
        or 0
    )
    warnings = [
        {
            "code": "endpoint_health",
            "message": f"{endpoint.name} is {endpoint.health_state}",
            "severity": "warning",
        }
        for endpoint in endpoint_warnings
    ]
    if offline_agents:
        warnings.append(
            {
                "code": "agent_offline",
                "message": f"{offline_agents} node agent(s) are offline",
                "severity": "warning",
            }
        )
    if reading.evidence in {"stale", "simulated"}:
        warnings.append(
            {
                "code": "carbon_evidence",
                "message": f"Grid evidence is {reading.evidence}",
                "severity": "info",
            }
        )
    return {
        "workspaceId": str(workspace.id),
        "window": window,
        "generatedAt": utcnow().isoformat(),
        "requests": total,
        "successRate": successes / total if total else 1.0,
        "cacheHitRate": cache_hits / total if total else 0.0,
        "actualCarbonGrams": float(impact[0]),
        "avoidedCarbonGrams": max(0, float(impact[1])),
        "actualCostUsd": float(impact[2]),
        "costDeltaUsd": float(impact[2] - impact[3]),
        "grid": reading.model_dump(mode="json"),
        "routeDistribution": [{"route": route, "count": count} for route, count in routes],
        "connectedNodes": agents,
        "activeProfiles": list(
            (
                await session.scalars(
                    select(NodeAgent.active_profile).where(NodeAgent.status == "online").distinct()
                )
            ).all()
        ),
        "carbonSeries": [
            {
                "time": point[0].isoformat(),
                "baseline": point[1],
                "actual": point[2],
            }
            for point in impact_points
        ],
        "energyByEndpoint": [
            {"endpoint": endpoint, "energyKwh": float(energy)}
            for endpoint, energy in endpoint_energy
        ],
        "qualityFallbackCount": quality_fallback_count,
        "warnings": warnings,
        "evidence": "simulated",
        "recentRequests": [
            {
                "id": str(item.id),
                "time": item.started_at.isoformat(),
                "model": item.requested_model_alias,
                "status": item.status,
                "cache": item.cache_status,
                "fallback": item.fallback_used,
                "durationMs": item.duration_ms,
            }
            for item in latest
        ],
    }


@router.get("/events")
async def events(
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    if event_connections.locked():
        raise EcoRouteError(
            "Too many event-stream connections",
            status_code=429,
            code="sse_connection_limit",
        )
    await event_connections.acquire()
    workspace = await _workspace(session)
    stream = f"ecoroute:events:{workspace.id}"

    async def generate() -> Any:
        try:
            current = last_event_id or "$"
            if last_event_id:
                earliest = await redis.xrange(stream, min="-", max="+", count=1)
                earliest_id = (
                    earliest[0][0].decode()
                    if earliest and isinstance(earliest[0][0], bytes)
                    else str(earliest[0][0])
                    if earliest
                    else None
                )
                if earliest_id is not None and last_event_id < earliest_id:
                    snapshot = {
                        "id": earliest_id,
                        "type": "snapshot",
                        "occurredAt": utcnow().isoformat(),
                        "workspaceId": str(workspace.id),
                        "data": {"reason": "last_event_id_expired", "refetch": ["overview"]},
                    }
                    current = earliest_id
                    yield f"id: {current}\ndata: {json.dumps(snapshot, separators=(',', ':'))}\n\n"
            while True:
                values: Any = await redis.xread({stream: current}, count=100, block=15_000)
                if not values:
                    yield ": heartbeat\n\n"
                    continue
                for _, entries in values:
                    for event_id, fields in entries:
                        current = event_id
                        envelope = {
                            "id": event_id,
                            "type": fields["type"],
                            "occurredAt": fields["occurredAt"],
                            "workspaceId": fields["workspaceId"],
                            "data": json.loads(fields["data"]),
                        }
                        yield f"id: {event_id}\ndata: {json.dumps(envelope, separators=(',', ':'))}\n\n"
        finally:
            event_connections.release()

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/model-endpoints")
async def list_endpoints(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cursor_id = _uuid_cursor(cursor, "endpoint")
    statement = (
        select(ModelEndpoint)
        .where(ModelEndpoint.deleted_at.is_(None))
        .order_by(ModelEndpoint.id.desc())
        .limit(limit + 1)
    )
    if cursor_id is not None:
        statement = statement.where(ModelEndpoint.id < cursor_id)
    items = list((await session.scalars(statement)).all())
    items, next_cursor = _uuid_page(items, limit)
    return {"items": [_endpoint_json(item) for item in items], "nextCursor": next_cursor}


@router.post("/model-endpoints", status_code=201)
async def create_endpoint(
    body: ModelEndpointCreate, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    workspace = await _workspace(session)
    payload = body.model_dump()
    if body.slm_profile_id is not None:
        profile = await session.get(SlmProfile, body.slm_profile_id)
        if (
            profile is None
            or profile.workspace_id != workspace.id
            or profile.deleted_at is not None
        ):
            raise EcoRouteError("SLM profile not found", status_code=404, code="not_found")
    payload["capabilities"] = sorted(payload["capabilities"])
    payload["fixed_request_kwh"] = float(payload["fixed_request_kwh"])
    payload["input_kwh_per_1k_tokens"] = float(payload["input_kwh_per_1k_tokens"])
    payload["output_kwh_per_1k_tokens"] = float(payload["output_kwh_per_1k_tokens"])
    endpoint = ModelEndpoint(
        workspace_id=workspace.id, coefficient_version="operator-v1", **payload
    )
    session.add(endpoint)
    await session.commit()
    return _endpoint_json(endpoint)


@router.get("/model-endpoints/{endpoint_id}")
async def get_endpoint(
    endpoint_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    endpoint = await session.get(ModelEndpoint, endpoint_id)
    if endpoint is None or endpoint.deleted_at is not None:
        raise EcoRouteError("Endpoint not found", status_code=404, code="not_found")
    return _endpoint_json(endpoint)


@router.patch("/model-endpoints/{endpoint_id}")
async def patch_endpoint(
    endpoint_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    endpoint = await session.get(ModelEndpoint, endpoint_id)
    if endpoint is None or endpoint.deleted_at is not None:
        raise EcoRouteError("Endpoint not found", status_code=404, code="not_found")
    current = {
        "name": endpoint.name,
        "provider": endpoint.provider,
        "baseUrl": endpoint.base_url,
        "credentialRef": endpoint.credential_ref,
        "physicalModel": endpoint.physical_model,
        "region": endpoint.region,
        "gridZone": endpoint.grid_zone,
        "qualityTier": endpoint.quality_tier,
        "capabilities": endpoint.capabilities,
        "contextWindowTokens": endpoint.context_window_tokens,
        "inputUsdPerMillionTokens": endpoint.input_usd_per_million_tokens,
        "outputUsdPerMillionTokens": endpoint.output_usd_per_million_tokens,
        "fixedRequestKwh": endpoint.fixed_request_kwh,
        "inputKwhPer1kTokens": endpoint.input_kwh_per_1k_tokens,
        "outputKwhPer1kTokens": endpoint.output_kwh_per_1k_tokens,
        "energyEvidence": endpoint.energy_evidence,
        "latencyP50Ms": endpoint.latency_p50_ms,
        "latencyP95Ms": endpoint.latency_p95_ms,
        "selfHosted": endpoint.self_hosted,
        "slmProfileId": endpoint.slm_profile_id,
        "enabled": endpoint.enabled,
        "baselineConcurrency": endpoint.baseline_concurrency,
        "concurrencyTarget": endpoint.concurrency_target,
    }
    validated = ModelEndpointCreate.model_validate({**current, **body}).model_dump()
    profile_id = validated.get("slm_profile_id")
    if profile_id is not None:
        profile = await session.get(SlmProfile, profile_id)
        if (
            profile is None
            or profile.workspace_id != endpoint.workspace_id
            or profile.deleted_at is not None
        ):
            raise EcoRouteError("SLM profile not found", status_code=404, code="not_found")
    validated["capabilities"] = sorted(validated["capabilities"])
    for key, value in validated.items():
        setattr(endpoint, key, float(value) if key.endswith("_kwh") else value)
    endpoint.version += 1
    await session.commit()
    return _endpoint_json(endpoint)


@router.delete("/model-endpoints/{endpoint_id}", status_code=204)
async def delete_endpoint(
    endpoint_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> None:
    endpoint = await session.get(ModelEndpoint, endpoint_id)
    if endpoint is None or endpoint.deleted_at is not None:
        raise EcoRouteError("Endpoint not found", status_code=404, code="not_found")
    referenced = await session.scalar(
        select(LogicalModel.id)
        .outerjoin(
            LogicalModelEndpoint,
            LogicalModelEndpoint.logical_model_id == LogicalModel.id,
        )
        .where(
            LogicalModel.deleted_at.is_(None),
            or_(
                LogicalModel.baseline_endpoint_id == endpoint_id,
                LogicalModel.required_fallback_endpoint_id == endpoint_id,
                LogicalModelEndpoint.endpoint_id == endpoint_id,
            ),
        )
        .limit(1)
    )
    if referenced is not None:
        raise EcoRouteError(
            "Detach the endpoint from every logical model before deleting it",
            status_code=409,
            code="endpoint_in_use",
        )
    endpoint.deleted_at = utcnow()
    endpoint.enabled = False
    await session.commit()


@router.post("/model-endpoints/{endpoint_id}/test")
async def test_endpoint(
    endpoint_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    endpoint = await session.get(ModelEndpoint, endpoint_id)
    if endpoint is None:
        raise EcoRouteError("Endpoint not found", status_code=404, code="not_found")
    started = datetime.now(timezone.utc)
    result = await providers.for_provider(endpoint.provider).health(endpoint)
    return {
        **result,
        "latencyMs": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        "capabilities": endpoint.capabilities,
    }


@router.get("/logical-models")
async def logical_models(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cursor_id = _uuid_cursor(cursor, "logical model")
    statement = (
        select(LogicalModel)
        .where(LogicalModel.deleted_at.is_(None))
        .order_by(LogicalModel.id.desc())
        .limit(limit + 1)
    )
    if cursor_id is not None:
        statement = statement.where(LogicalModel.id < cursor_id)
    items = list((await session.scalars(statement)).all())
    items, next_cursor = _uuid_page(items, limit)
    model_ids = [item.id for item in items]
    pools = (
        list(
            (
                await session.execute(
                    select(LogicalModelEndpoint).where(
                        LogicalModelEndpoint.logical_model_id.in_(model_ids)
                    )
                )
            ).scalars()
        )
        if model_ids
        else []
    )
    by_model: dict[uuid.UUID, list[uuid.UUID]] = {}
    for pool in pools:
        by_model.setdefault(pool.logical_model_id, []).append(pool.endpoint_id)
    return {
        "items": [_logical_model_json(item, by_model.get(item.id, [])) for item in items],
        "nextCursor": next_cursor,
    }


@router.post("/logical-models", status_code=201)
async def create_logical_model(
    body: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    workspace = await _workspace(session)
    alias = str(body.get("alias", "")).strip()
    if not alias or len(alias) > 120:
        raise EcoRouteError("A bounded alias is required", code="invalid_alias")
    if await session.scalar(
        select(LogicalModel).where(
            LogicalModel.workspace_id == workspace.id,
            LogicalModel.alias == alias,
        )
    ):
        raise EcoRouteError("Logical model alias already exists", status_code=409, code="conflict")
    endpoint_ids = [_uuid_field(value, "endpointIds") for value in body.get("endpointIds", [])]
    if not endpoint_ids:
        raise EcoRouteError("At least one endpoint is required", code="endpoint_pool_required")
    baseline = _uuid_field(body.get("baselineEndpointId", endpoint_ids[0]), "baselineEndpointId")
    fallback = _uuid_field(
        body.get("requiredFallbackEndpointId", baseline), "requiredFallbackEndpointId"
    )
    if not body.get("activePolicyId"):
        raise EcoRouteError("activePolicyId is required", code="policy_required")
    active_policy_id = _uuid_field(body["activePolicyId"], "activePolicyId")
    await _validate_logical_references(
        session,
        workspace_id=workspace.id,
        endpoint_ids=endpoint_ids,
        baseline_id=baseline,
        fallback_id=fallback,
        policy_id=active_policy_id,
    )
    logical = LogicalModel(
        workspace_id=workspace.id,
        alias=alias,
        display_name=str(body.get("displayName", alias))[:200],
        baseline_endpoint_id=baseline,
        required_fallback_endpoint_id=fallback,
        active_policy_id=active_policy_id,
        enabled=bool(body.get("enabled", True)),
    )
    session.add(logical)
    await session.flush()
    for priority, endpoint_id in enumerate(endpoint_ids, start=1):
        session.add(
            LogicalModelEndpoint(
                logical_model_id=logical.id,
                endpoint_id=endpoint_id,
                priority=priority * 10,
            )
        )
    await session.commit()
    return _logical_model_json(logical, endpoint_ids)


@router.get("/logical-models/{model_id}")
async def get_logical_model(
    model_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    logical = await session.get(LogicalModel, model_id)
    if logical is None or logical.deleted_at is not None:
        raise EcoRouteError("Logical model not found", status_code=404, code="not_found")
    endpoint_ids = list(
        (
            await session.scalars(
                select(LogicalModelEndpoint.endpoint_id)
                .where(LogicalModelEndpoint.logical_model_id == model_id)
                .order_by(LogicalModelEndpoint.priority)
            )
        ).all()
    )
    return _logical_model_json(logical, endpoint_ids)


@router.patch("/logical-models/{model_id}")
async def patch_logical_model(
    model_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    logical = await session.get(LogicalModel, model_id)
    if logical is None or logical.deleted_at is not None:
        raise EcoRouteError("Logical model not found", status_code=404, code="not_found")
    endpoint_ids = [_uuid_field(value, "endpointIds") for value in body.get("endpointIds", [])]
    if "endpointIds" in body and not endpoint_ids:
        raise EcoRouteError("At least one endpoint is required", code="endpoint_pool_required")
    if "endpointIds" not in body:
        endpoint_ids = list(
            (
                await session.scalars(
                    select(LogicalModelEndpoint.endpoint_id).where(
                        LogicalModelEndpoint.logical_model_id == model_id
                    )
                )
            ).all()
        )
    baseline = logical.baseline_endpoint_id
    fallback = logical.required_fallback_endpoint_id
    assert baseline is not None and fallback is not None and logical.active_policy_id is not None
    for external, internal in {
        "displayName": "display_name",
        "enabled": "enabled",
        "baselineEndpointId": "baseline_endpoint_id",
        "requiredFallbackEndpointId": "required_fallback_endpoint_id",
    }.items():
        if external in body:
            value = body[external]
            if internal.endswith("_id"):
                value = _uuid_field(value, external)
                if value not in set(endpoint_ids):
                    raise EcoRouteError(
                        "Baseline and fallback must be in the endpoint pool", code="invalid_pool"
                    )
            setattr(logical, internal, value)
            if internal == "baseline_endpoint_id":
                baseline = value
            elif internal == "required_fallback_endpoint_id":
                fallback = value
    await _validate_logical_references(
        session,
        workspace_id=logical.workspace_id,
        endpoint_ids=endpoint_ids,
        baseline_id=baseline,
        fallback_id=fallback,
        policy_id=logical.active_policy_id,
    )
    if "endpointIds" in body:
        await session.execute(
            delete(LogicalModelEndpoint).where(LogicalModelEndpoint.logical_model_id == model_id)
        )
        for priority, endpoint_id in enumerate(endpoint_ids, start=1):
            session.add(
                LogicalModelEndpoint(
                    logical_model_id=model_id,
                    endpoint_id=endpoint_id,
                    priority=priority * 10,
                )
            )
    logical.version += 1
    await session.commit()
    return _logical_model_json(logical, endpoint_ids)


@router.get("/policies")
async def policies(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cursor_id = _uuid_cursor(cursor, "policy")
    statement = select(RoutingPolicy).order_by(RoutingPolicy.id.desc()).limit(limit + 1)
    if cursor_id is not None:
        statement = statement.where(RoutingPolicy.id < cursor_id)
    items = list((await session.scalars(statement)).all())
    items, next_cursor = _uuid_page(items, limit)
    return {"items": [_policy_json(item) for item in items], "nextCursor": next_cursor}


@router.post("/policies", status_code=201)
async def create_policy(
    body: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    workspace = await _workspace(session)
    config = RoutingPolicyConfig.model_validate(body.get("config", body))
    policy = RoutingPolicy(
        workspace_id=workspace.id,
        family_id=uuid7(),
        version_number=1,
        name=str(body.get("name", config.name))[:200],
        preset=config.preset,
        config=config.model_dump(mode="json"),
        created_by=str(body.get("createdBy", "demo-operator"))[:100],
    )
    session.add(policy)
    await session.commit()
    return _policy_json(policy)


@router.get("/policies/{policy_id}")
async def get_policy(
    policy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    policy = await session.get(RoutingPolicy, policy_id)
    if policy is None:
        raise EcoRouteError("Policy not found", status_code=404, code="not_found")
    return _policy_json(policy)


@router.post("/policies/{policy_id}/clone", status_code=201)
async def clone_policy(
    policy_id: uuid.UUID,
    body: dict[str, Any] = Body(default_factory=dict),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    source = await session.get(RoutingPolicy, policy_id)
    if source is None:
        raise EcoRouteError("Policy not found", status_code=404, code="not_found")
    max_version = int(
        await session.scalar(
            select(func.max(RoutingPolicy.version_number)).where(
                RoutingPolicy.family_id == source.family_id
            )
        )
        or source.version_number
    )
    config = RoutingPolicyConfig.model_validate(body.get("config", source.config))
    clone = RoutingPolicy(
        workspace_id=source.workspace_id,
        family_id=source.family_id,
        version_number=max_version + 1,
        name=body.get("name", source.name),
        preset=config.preset,
        config=config.model_dump(mode="json"),
    )
    session.add(clone)
    await session.commit()
    return {"id": str(clone.id), "versionNumber": clone.version_number, "config": clone.config}


@router.post("/logical-models/{model_id}/activate-policy")
async def activate_policy(
    model_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    logical = await session.get(LogicalModel, model_id)
    policy_id = _uuid_field(body.get("policyId"), "policyId")
    policy = await session.get(RoutingPolicy, policy_id)
    if logical is None or policy is None or logical.workspace_id != policy.workspace_id:
        raise EcoRouteError("Model or policy not found", status_code=404, code="not_found")
    endpoint_ids = list(
        (
            await session.scalars(
                select(LogicalModelEndpoint.endpoint_id).where(
                    LogicalModelEndpoint.logical_model_id == logical.id
                )
            )
        ).all()
    )
    assert logical.baseline_endpoint_id is not None
    assert logical.required_fallback_endpoint_id is not None
    await _validate_logical_references(
        session,
        workspace_id=logical.workspace_id,
        endpoint_ids=endpoint_ids,
        baseline_id=logical.baseline_endpoint_id,
        fallback_id=logical.required_fallback_endpoint_id,
        policy_id=policy.id,
    )
    logical.active_policy_id = policy_id
    logical.version += 1
    await session.commit()
    return {"logicalModelId": str(logical.id), "activePolicyId": str(policy_id)}


@router.post("/policies/{policy_id}/simulate")
async def simulate_policy(
    policy_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    policy_row = await session.get(RoutingPolicy, policy_id)
    if policy_row is None:
        raise EcoRouteError("Policy not found", status_code=404, code="not_found")
    logical = await session.scalar(
        select(LogicalModel).where(LogicalModel.active_policy_id == policy_id)
    )
    if logical is None:
        logical = await session.scalar(select(LogicalModel).limit(1))
    assert logical is not None
    request = ChatCompletionRequest(
        model=logical.alias, messages=[{"role": "user", "content": body.get("prompt", "")}]
    )
    features = normalize_request(uuid7(), request)
    classification = deterministic_classify(features)
    endpoint_ids = list(
        (
            await session.scalars(
                select(LogicalModelEndpoint.endpoint_id).where(
                    LogicalModelEndpoint.logical_model_id == logical.id
                )
            )
        ).all()
    )
    endpoint_rows = list(
        (
            await session.scalars(select(ModelEndpoint).where(ModelEndpoint.id.in_(endpoint_ids)))
        ).all()
    )
    scenario = await _redis_text(redis, "ecoroute:demo:grid", "moderate")
    provider = FixtureCarbonProvider(scenario)
    profile_ids = {endpoint.slm_profile_id for endpoint in endpoint_rows if endpoint.slm_profile_id}
    profiles = {
        profile.id: profile
        for profile in (
            await session.scalars(select(SlmProfile).where(SlmProfile.id.in_(profile_ids)))
        ).all()
    }
    candidates = []
    for endpoint in endpoint_rows:
        reading = await provider.reading(endpoint.grid_zone)
        candidates.append(
            _candidate(
                endpoint,
                reading.intensity_gco2_kwh,
                profiles.get(endpoint.slm_profile_id) if endpoint.slm_profile_id else None,
            )
        )
    baseline = next(item for item in candidates if item.id == logical.baseline_endpoint_id)
    selected, snapshots, reason = select_candidate(
        candidates,
        features,
        classification,
        RoutingPolicyConfig.model_validate(policy_row.config),
        baseline,
        logical.required_fallback_endpoint_id,  # type: ignore[arg-type]
    )
    return {
        "evidence": "simulated",
        "classification": classification.model_dump(mode="json"),
        "candidates": [item.model_dump(mode="json") for item in snapshots],
        "selectedEndpointId": str(selected.id),
        "selectionReason": reason,
    }


@router.get("/requests")
async def requests(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    started_after: datetime | None = Query(None, alias="from"),
    started_before: datetime | None = Query(None, alias="to"),
    route: str | None = None,
    status: str | None = None,
    endpoint_id: uuid.UUID | None = Query(None, alias="endpointId"),
    cache: str | None = None,
    fallback: bool | None = None,
    demo_session_id: str | None = Query(None, alias="demoSessionId"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    statement = (
        select(GatewayRequest, ModelEndpoint, ImpactRecord)
        .outerjoin(ModelEndpoint, ModelEndpoint.id == GatewayRequest.selected_endpoint_id)
        .outerjoin(
            ImpactRecord,
            and_(
                ImpactRecord.request_id == GatewayRequest.id,
                ImpactRecord.strategy == "end_to_end",
            ),
        )
        .order_by(GatewayRequest.started_at.desc(), GatewayRequest.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        try:
            timestamp_text, request_id_text = cursor.rsplit("|", 1)
            cursor_time = datetime.fromisoformat(timestamp_text)
            cursor_id = uuid.UUID(request_id_text)
        except (ValueError, TypeError) as exc:
            raise EcoRouteError("Invalid request cursor", code="invalid_cursor") from exc
        statement = statement.where(
            or_(
                GatewayRequest.started_at < cursor_time,
                and_(
                    GatewayRequest.started_at == cursor_time,
                    GatewayRequest.id < cursor_id,
                ),
            )
        )
    if started_after:
        statement = statement.where(GatewayRequest.started_at >= started_after)
    if started_before:
        statement = statement.where(GatewayRequest.started_at <= started_before)
    if route:
        statement = (
            statement.where(GatewayRequest.cache_status.in_(["exact", "semantic"]))
            if route == "cache"
            else statement.where(ModelEndpoint.name == route)
        )
    if status:
        statement = statement.where(GatewayRequest.status == status)
    if endpoint_id:
        statement = statement.where(GatewayRequest.selected_endpoint_id == endpoint_id)
    if cache:
        statement = statement.where(GatewayRequest.cache_status == cache)
    if fallback is not None:
        statement = statement.where(GatewayRequest.fallback_used == fallback)
    if demo_session_id:
        statement = statement.where(
            GatewayRequest.client_metadata["demo_session_id"].astext == demo_session_id
        )
    rows = list((await session.execute(statement)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    last = rows[-1][0] if rows else None
    return {
        "items": [
            {
                "id": str(item.id),
                "startedAt": item.started_at.isoformat(),
                "logicalModel": item.requested_model_alias,
                "status": item.status,
                "cache": item.cache_status,
                "routerClassification": item.router_classification,
                "route": (
                    "cache"
                    if item.cache_status in {"exact", "semantic"}
                    else endpoint.name
                    if endpoint
                    else "unknown"
                ),
                "endpoint": endpoint.name if endpoint else None,
                "selectedEndpointId": str(item.selected_endpoint_id)
                if item.selected_endpoint_id
                else None,
                "fallbackUsed": item.fallback_used,
                "durationMs": item.duration_ms,
                "costUsd": float(impact.actual_cost_usd) if impact else 0.0,
                "carbonGrams": impact.actual_carbon_g if impact else 0.0,
                "evidence": (
                    impact.evidence.get("carbon_level", "estimated") if impact else "estimated"
                ),
                "clientMetadata": item.client_metadata,
                "redactedPreview": item.redacted_prompt_preview,
            }
            for item, endpoint, impact in rows
        ],
        "nextCursor": f"{last.started_at.isoformat()}|{last.id}"
        if last is not None and has_more
        else None,
    }


@router.get("/requests/{request_id}")
async def request_detail(
    request_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    item = await session.get(GatewayRequest, request_id)
    if item is None:
        raise EcoRouteError("Request not found", status_code=404, code="not_found")
    attempts = list(
        (
            await session.scalars(select(ModelAttempt).where(ModelAttempt.request_id == request_id))
        ).all()
    )
    impacts = list(
        (
            await session.scalars(select(ImpactRecord).where(ImpactRecord.request_id == request_id))
        ).all()
    )
    decision = await session.scalar(
        select(RouteDecision).where(RouteDecision.request_id == request_id)
    )
    selected_endpoint = (
        await session.get(ModelEndpoint, item.selected_endpoint_id)
        if item.selected_endpoint_id
        else None
    )
    return {
        "id": str(item.id),
        "status": item.status,
        "logicalModel": item.requested_model_alias,
        "requestFeatures": item.request_features,
        "routerClassification": item.router_classification,
        "redactedPreview": item.redacted_prompt_preview,
        "cache": item.cache_status,
        "selectedEndpointId": str(item.selected_endpoint_id) if item.selected_endpoint_id else None,
        "selectedEndpoint": selected_endpoint.name if selected_endpoint else None,
        "fallbackUsed": item.fallback_used,
        "durationMs": item.duration_ms,
        "timeline": [
            {"stage": "request.received", "at": item.started_at.isoformat()},
            *(
                [{"stage": "stream.first_token", "at": item.first_token_at.isoformat()}]
                if item.first_token_at
                else []
            ),
            *(
                [{"stage": f"request.{item.status}", "at": item.completed_at.isoformat()}]
                if item.completed_at
                else []
            ),
        ],
        "routeDecision": (
            {
                "policyId": str(decision.policy_id),
                "gridState": decision.grid_state,
                "candidates": decision.candidate_snapshot,
                "selectedEndpointId": str(decision.selected_endpoint_id)
                if decision.selected_endpoint_id
                else None,
                "selectionReason": decision.selection_reason,
                "scoreBreakdown": decision.score_breakdown,
                "createdAt": decision.created_at.isoformat(),
            }
            if decision
            else None
        ),
        "attempts": [
            {
                "number": attempt.attempt_number,
                "endpointId": str(attempt.endpoint_id),
                "purpose": attempt.purpose,
                "status": attempt.status,
                "durationMs": attempt.duration_ms,
                "inputTokens": attempt.input_tokens,
                "outputTokens": attempt.output_tokens,
                "upstreamRequestId": attempt.upstream_request_id,
                "qualityVerdict": attempt.quality_verdict,
                "errorCode": attempt.error_code,
                "startedAt": attempt.started_at.isoformat(),
                "completedAt": attempt.completed_at.isoformat() if attempt.completed_at else None,
            }
            for attempt in attempts
        ],
        "impact": [
            {
                "strategy": value.strategy,
                "baselineEnergyKwh": value.baseline_energy_kwh,
                "actualEnergyKwh": value.actual_energy_kwh,
                "baselineCarbonG": value.baseline_carbon_g,
                "actualCarbonG": value.actual_carbon_g,
                "rawCarbonDeltaG": value.raw_carbon_delta_g,
                "baselineCostUsd": str(value.baseline_cost_usd),
                "actualCostUsd": str(value.actual_cost_usd),
                "evidence": value.evidence,
            }
            for value in impacts
        ],
    }


@router.get("/requests/{request_id}/attempts")
async def request_attempts(
    request_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: int | None = Query(None, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if await session.get(GatewayRequest, request_id) is None:
        raise EcoRouteError("Request not found", status_code=404, code="not_found")
    statement = (
        select(ModelAttempt)
        .where(ModelAttempt.request_id == request_id)
        .order_by(ModelAttempt.attempt_number)
        .limit(limit + 1)
    )
    if cursor is not None:
        statement = statement.where(ModelAttempt.attempt_number > cursor)
    items = list((await session.scalars(statement)).all())
    has_more = len(items) > limit
    items = items[:limit]
    return {
        "items": [
            {"attemptNumber": item.attempt_number, "qualityVerdict": item.quality_verdict}
            for item in items
        ],
        "nextCursor": str(items[-1].attempt_number) if has_more and items else None,
    }


@router.get("/cache/stats")
async def cache_stats(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    now = utcnow()
    entries, entry_hits, bytes_estimate = (
        await session.execute(
            select(
                func.count(CacheEntry.id),
                func.coalesce(func.sum(CacheEntry.hit_count), 0),
                func.coalesce(func.sum(func.pg_column_size(CacheEntry.completion)), 0),
            ).where(CacheEntry.invalidated_at.is_(None), CacheEntry.expires_at > now)
        )
    ).one()
    exact_hits = int(
        await session.scalar(
            select(func.count(GatewayRequest.id)).where(GatewayRequest.cache_status == "exact")
        )
        or 0
    )
    semantic_hits = int(
        await session.scalar(
            select(func.count(GatewayRequest.id)).where(GatewayRequest.cache_status == "semantic")
        )
        or 0
    )
    completed = int(
        await session.scalar(
            select(func.count(GatewayRequest.id)).where(GatewayRequest.status == "completed")
        )
        or 0
    )
    invalidations = int(
        await session.scalar(
            select(func.count(CacheEntry.id)).where(CacheEntry.invalidated_at.is_not(None))
        )
        or 0
    )
    savings = float(
        await session.scalar(
            select(func.coalesce(func.sum(ImpactRecord.raw_carbon_delta_g), 0)).where(
                ImpactRecord.strategy == "end_to_end",
                ImpactRecord.actual_energy_kwh <= 0.000001,
            )
        )
        or 0
    )
    reading = await session.scalar(
        select(CarbonReadingRecord).order_by(CarbonReadingRecord.observed_at.desc()).limit(1)
    )
    intensity = reading.intensity_gco2_kwh if reading else 275.0
    grid_state = "clean" if intensity <= 150 else "dirty" if intensity >= 400 else "moderate"
    capacity = {"clean": 50, "moderate": 75, "dirty": 100}[grid_state]
    total_hits = exact_hits + semantic_hits
    return {
        "entries": int(entries),
        "hits": int(entry_hits),
        "exactHits": exact_hits,
        "semanticHits": semantic_hits,
        "hitRate": total_hits / completed if completed else 0.0,
        "bytesEstimate": int(bytes_estimate),
        "estimatedSavingsGrams": max(0.0, savings),
        "invalidations": invalidations,
        "gridState": grid_state,
        "gridIntensityGco2Kwh": intensity,
        "capacityTargetPct": capacity,
        "evidence": reading.evidence if reading else "simulated",
        "gridPolicy": "carbon-aware dynamic TTL; similarity threshold unchanged",
    }


@router.get("/cache/entries")
async def cache_entries(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cursor_id = _uuid_cursor(cursor, "cache entry")
    statement = select(CacheEntry).order_by(CacheEntry.id.desc()).limit(limit + 1)
    if cursor_id is not None:
        statement = statement.where(CacheEntry.id < cursor_id)
    items = list((await session.scalars(statement)).all())
    items, next_cursor = _uuid_page(items, limit)
    return {
        "items": [
            {
                "id": str(item.id),
                "fingerprint": item.exact_fingerprint,
                "redactedPreview": item.normalized_semantic_text[:120],
                "hitCount": item.hit_count,
                "expiresAt": item.expires_at.isoformat(),
                "invalidatedAt": item.invalidated_at.isoformat() if item.invalidated_at else None,
            }
            for item in items
        ],
        "nextCursor": next_cursor,
    }


@router.post("/cache/entries/{entry_id}/invalidate")
async def invalidate_entry(
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    entry = await session.get(CacheEntry, entry_id)
    if entry is None:
        raise EcoRouteError("Cache entry not found", status_code=404, code="not_found")
    entry.invalidated_at = utcnow()
    await redis.delete(f"ecoroute:exact:{entry.workspace_id}:{entry.exact_fingerprint}")
    await session.commit()
    await publish_event(
        redis,
        settings,
        entry.workspace_id,
        "cache.invalidated",
        {"entryId": str(entry.id), "scope": "entry", "count": 1},
    )
    return {"invalidated": 1}


@router.post("/cache/invalidate")
async def invalidate_cache(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    scope = body.get("scope")
    if scope not in {"all", "logical_model", "slm_profile"}:
        raise EcoRouteError("A typed invalidation scope is required", code="invalid_scope")
    statement = select(CacheEntry).where(CacheEntry.invalidated_at.is_(None))
    if scope == "logical_model":
        if not body.get("logicalModelId"):
            raise EcoRouteError("logicalModelId is required", code="invalid_scope")
        statement = statement.where(
            CacheEntry.logical_model_id == uuid.UUID(body["logicalModelId"])
        )
    elif scope == "slm_profile":
        if not body.get("slmProfileId"):
            raise EcoRouteError("slmProfileId is required", code="invalid_scope")
        statement = statement.join(
            ModelEndpoint, ModelEndpoint.id == CacheEntry.source_endpoint_id
        ).where(ModelEndpoint.slm_profile_id == uuid.UUID(body["slmProfileId"]))
    entries = list((await session.scalars(statement)).all())
    preview = {
        "scope": scope,
        "expectedCount": len(entries),
        "logicalModelId": body.get("logicalModelId"),
        "slmProfileId": body.get("slmProfileId"),
    }
    if not body.get("confirm"):
        return {"preview": preview, "invalidated": 0, "requiresConfirmation": True}
    supplied_count = body.get("expectedCount")
    if supplied_count is not None and int(supplied_count) != len(entries):
        raise EcoRouteError(
            "Cache contents changed after preview; request a new preview",
            status_code=409,
            code="preview_count_changed",
            details={"expectedCount": len(entries)},
        )
    invalidated_at = utcnow()
    for entry in entries:
        entry.invalidated_at = invalidated_at
    keys = [f"ecoroute:exact:{entry.workspace_id}:{entry.exact_fingerprint}" for entry in entries]
    if keys:
        await redis.delete(*keys)
    await session.commit()
    workspace = await _workspace(session)
    await publish_event(
        redis,
        settings,
        workspace.id,
        "cache.invalidated",
        {**preview, "count": len(entries)},
    )
    return {"preview": preview, "invalidated": len(entries)}


@router.get("/slm-profiles")
async def slm_profiles(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cursor_id = _uuid_cursor(cursor, "SLM profile")
    statement = (
        select(SlmProfile)
        .where(SlmProfile.deleted_at.is_(None))
        .order_by(SlmProfile.id.desc())
        .limit(limit + 1)
    )
    if cursor_id is not None:
        statement = statement.where(SlmProfile.id < cursor_id)
    items = list((await session.scalars(statement)).all())
    items, next_cursor = _uuid_page(items, limit)
    return {"items": [_profile_json(item) for item in items], "nextCursor": next_cursor}


@router.post("/slm-profiles", status_code=201)
async def create_slm_profile(
    body: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    workspace = await _workspace(session)
    name = str(body.get("name", "")).strip()
    business_name = str(body.get("businessName", "")).strip()
    if not name or not business_name:
        raise EcoRouteError("name and businessName are required", code="invalid_profile")
    definition = body.get("definition", {})
    if not isinstance(definition, dict):
        raise EcoRouteError("definition must be an object", code="invalid_profile")
    profile = SlmProfile(
        workspace_id=workspace.id,
        name=name[:200],
        description=str(body.get("description", ""))[:10_000],
        business_name=business_name[:200],
        definition=definition,
        status="draft",
    )
    session.add(profile)
    await session.flush()
    for document in body.get("policyDocuments", []):
        content = str(document.get("content", ""))
        key = str(document.get("policyKey", "")).strip()
        if not key or not content:
            raise EcoRouteError(
                "Each policy document needs policyKey and content", code="invalid_policy_document"
            )
        session.add(
            PolicyDocument(
                slm_profile_id=profile.id,
                policy_key=key[:100],
                title=str(document.get("title", key))[:200],
                content=content,
                version=1,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            )
        )
    await session.commit()
    documents = list(
        (
            await session.scalars(
                select(PolicyDocument).where(PolicyDocument.slm_profile_id == profile.id)
            )
        ).all()
    )
    return _profile_json(profile, documents)


@router.get("/slm-profiles/{profile_id}")
async def get_slm_profile(
    profile_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    profile = await session.get(SlmProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise EcoRouteError("SLM profile not found", status_code=404, code="not_found")
    documents = list(
        (
            await session.scalars(
                select(PolicyDocument).where(
                    PolicyDocument.slm_profile_id == profile_id,
                    PolicyDocument.active.is_(True),
                )
            )
        ).all()
    )
    return _profile_json(profile, documents)


@router.patch("/slm-profiles/{profile_id}")
async def patch_slm_profile(
    profile_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    profile = await session.get(SlmProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise EcoRouteError("SLM profile not found", status_code=404, code="not_found")
    if profile.status not in {"draft", "ready", "experimental"}:
        raise EcoRouteError(
            "Only draft or inactive profiles can be edited",
            status_code=409,
            code="invalid_state_transition",
        )
    content_changed = False
    for external, internal, maximum in (
        ("name", "name", 200),
        ("description", "description", 10_000),
        ("businessName", "business_name", 200),
    ):
        if external in body:
            value = str(body[external])[:maximum]
            content_changed = content_changed or value != getattr(profile, internal)
            setattr(profile, internal, value)
    if "definition" in body:
        if not isinstance(body["definition"], dict):
            raise EcoRouteError("definition must be an object", code="invalid_profile")
        content_changed = content_changed or body["definition"] != profile.definition
        profile.definition = body["definition"]
    for document in body.get("policyDocuments", []):
        key = str(document.get("policyKey", "")).strip()
        content = str(document.get("content", ""))
        if not key or not content:
            raise EcoRouteError(
                "Each policy document needs policyKey and content", code="invalid_policy_document"
            )
        active = await session.scalar(
            select(PolicyDocument).where(
                PolicyDocument.slm_profile_id == profile_id,
                PolicyDocument.policy_key == key,
                PolicyDocument.active.is_(True),
            )
        )
        digest = hashlib.sha256(content.encode()).hexdigest()
        if active is not None and active.content_sha256 == digest:
            continue
        if active is not None:
            active.active = False
            version = active.version + 1
        else:
            version = 1
        session.add(
            PolicyDocument(
                slm_profile_id=profile_id,
                policy_key=key[:100],
                title=str(document.get("title", key))[:200],
                content=content,
                version=version,
                content_sha256=digest,
            )
        )
        content_changed = True
    if content_changed:
        profile.content_version += 1
        profile.version += 1
        profile.status = "draft"
        related_models = list(
            (
                await session.scalars(select(LogicalModel).where(LogicalModel.deleted_at.is_(None)))
            ).all()
        )
        for logical in related_models:
            policy = await session.get(RoutingPolicy, logical.active_policy_id)
            if policy and any(
                str(profile_id) == str(rule.get("slmProfileId"))
                for rule in policy.config.get("task_rules", [])
            ):
                policy.config = {
                    **policy.config,
                    "namespace_version": int(policy.config.get("namespace_version", 1)) + 1,
                }
    await session.commit()
    return await get_slm_profile(profile_id, session)


@router.post("/slm-profiles/{profile_id}/generate-dataset", status_code=202)
async def generate_dataset(
    profile_id: uuid.UUID,
    body: dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    profile = await session.get(SlmProfile, profile_id)
    if profile is None:
        raise EcoRouteError("SLM profile not found", status_code=404, code="not_found")
    request_payload = {
        "profile_id": str(profile.id),
        "target": min(int(body.get("target", 100)), 2_000),
        "distribution": body.get("distribution", {}),
    }
    existing_job = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing_job is not None:
        if existing_job.kind != "dataset.generate" or existing_job.input.get(
            "client_request_fingerprint"
        ) != _body_fingerprint(request_payload):
            raise EcoRouteError(
                "Idempotency-Key was already used with a different request",
                status_code=409,
                code="idempotency_conflict",
            )
        return {
            "datasetId": existing_job.input["dataset_id"],
            "jobId": str(existing_job.id),
            "status": existing_job.status,
            "geminiConfigured": bool(settings.gemini_api_key),
        }
    latest = int(
        await session.scalar(
            select(func.max(Dataset.version)).where(Dataset.slm_profile_id == profile_id)
        )
        or 0
    )
    dataset = Dataset(
        workspace_id=profile.workspace_id,
        slm_profile_id=profile.id,
        kind="support_slm",
        version=latest + 1,
        status="generating",
        generation_config={
            "target": request_payload["target"],
            "distribution": request_payload["distribution"],
            "profile_content_version": profile.content_version,
        },
    )
    session.add(dataset)
    await session.flush()
    await session.commit()
    job = await _enqueue_job(
        session,
        redis,
        workspace_id=profile.workspace_id,
        kind="dataset.generate",
        idempotency_key=idempotency_key,
        payload={
            **request_payload,
            "dataset_id": str(dataset.id),
            "client_request_fingerprint": _body_fingerprint(request_payload),
        },
    )
    return {
        "datasetId": str(dataset.id),
        "jobId": str(job.id),
        "status": "queued",
        "geminiConfigured": bool(settings.gemini_api_key),
    }


@router.post("/datasets/import", status_code=201)
async def import_dataset(
    body: dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    workspace = await _workspace(session)
    fingerprint = _body_fingerprint(body)
    existing = await session.scalar(
        select(Dataset).where(
            Dataset.generation_config["idempotency_key"].astext == idempotency_key
        )
    )
    if existing is not None:
        if existing.generation_config.get("request_fingerprint") != fingerprint:
            raise EcoRouteError(
                "Idempotency-Key was already used with a different import",
                status_code=409,
                code="idempotency_conflict",
            )
        return {
            "id": str(existing.id),
            "kind": existing.kind,
            "version": existing.version,
            "status": existing.status,
            "exampleCount": existing.example_count,
        }
    kind = str(body.get("kind", ""))
    if kind not in {"router", "support_slm"}:
        raise EcoRouteError("kind must be router or support_slm", code="invalid_dataset_kind")
    profile: SlmProfile | None = None
    if kind == "support_slm":
        try:
            profile_id = uuid.UUID(str(body["slmProfileId"]))
        except (KeyError, ValueError) as exc:
            raise EcoRouteError(
                "support_slm imports require slmProfileId", code="invalid_slm_profile"
            ) from exc
        profile = await session.get(SlmProfile, profile_id)
        if profile is None or profile.deleted_at is not None:
            raise EcoRouteError("SLM profile not found", status_code=404, code="not_found")
    values = body.get("examples")
    if not isinstance(values, list) or not 1 <= len(values) <= 5_000:
        raise EcoRouteError("examples must contain 1-5000 records", code="invalid_dataset")
    policy_ids = (
        set(
            (
                await session.scalars(
                    select(PolicyDocument.policy_key).where(
                        PolicyDocument.slm_profile_id == profile.id,
                        PolicyDocument.active.is_(True),
                    )
                )
            ).all()
        )
        if profile
        else set()
    )
    embedder = get_local_embedder(settings.embedding_model, settings.use_sentence_transformers)
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise EcoRouteError(f"examples[{index}] must be an object", code="invalid_example")
        input_value = " ".join(str(value.get("input", "")).split())
        if not 3 <= len(input_value) <= 4_000:
            raise EcoRouteError(
                f"examples[{index}].input must be 3-4000 characters", code="invalid_example"
            )
        _, contains_pii, contains_secret, _, uncertain = redact(input_value)
        if contains_pii or contains_secret or uncertain:
            raise EcoRouteError(
                f"examples[{index}].input contains an identifier, secret, or ambiguous number",
                code="unsafe_training_input",
            )
        normalized = input_value.casefold()
        if normalized in seen:
            raise EcoRouteError(
                f"examples[{index}] duplicates another input", code="duplicate_example"
            )
        seen.add(normalized)
        output = value.get("output")
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError as exc:
                raise EcoRouteError(
                    f"examples[{index}].output is invalid JSON", code="invalid_example"
                ) from exc
        if not isinstance(output, dict):
            raise EcoRouteError(
                f"examples[{index}].output must be an object", code="invalid_example"
            )
        if kind == "router":
            try:
                output = RouterClassification.model_validate(output).model_dump()
            except ValueError as exc:
                raise EcoRouteError(
                    f"examples[{index}] has an invalid router target", code="invalid_example"
                ) from exc
        else:
            if set(output) != {"answer", "confidence", "policy_ids", "needs_human"}:
                raise EcoRouteError(
                    f"examples[{index}] has an invalid support target", code="invalid_example"
                )
            referenced = output.get("policy_ids")
            if not isinstance(referenced, list) or not set(referenced).issubset(policy_ids):
                raise EcoRouteError(
                    f"examples[{index}] references an unknown policy", code="invalid_example"
                )
            if (
                not isinstance(output.get("answer"), str)
                or not isinstance(output.get("needs_human"), bool)
                or not isinstance(output.get("confidence"), (int, float))
                or not 0 <= float(output["confidence"]) <= 1
            ):
                raise EcoRouteError(
                    f"examples[{index}] has an invalid support target", code="invalid_example"
                )
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise EcoRouteError(
                f"examples[{index}].metadata must be an object", code="invalid_example"
            )
        group = str(metadata.get("paraphrase_group") or normalized)
        embedding = embedder.encode(input_value)
        for prior in prepared:
            if cosine_similarity(embedding, prior["embedding"]) > 0.97 and group != prior["group"]:
                raise EcoRouteError(
                    f"examples[{index}] is a near-duplicate without a shared paraphrase group",
                    code="duplicate_example",
                )
        bucket = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 100
        split = str(
            value.get("split") or ("train" if bucket < 70 else "eval" if bucket < 85 else "test")
        )
        if split not in {"train", "eval", "test"}:
            raise EcoRouteError(f"examples[{index}] has an invalid split", code="invalid_example")
        external_id = str(
            value.get("id")
            or f"{'router' if kind == 'router' else 'support'}_{hashlib.sha256((group + chr(0) + normalized).encode()).hexdigest()[:16]}"
        )[:100]
        prepared.append(
            {
                "external_id": external_id,
                "split": split,
                "input": input_value,
                "output": output,
                "metadata": {
                    **metadata,
                    "id": external_id,
                    "source": str(metadata.get("source", "manual_import")),
                    "review": "pending",
                    "approved": False,
                },
                "embedding": embedding,
                "group": group,
            }
        )
    dataset_profile_id: uuid.UUID | None = profile.id if profile else None
    version_filter = [Dataset.kind == kind]
    version_filter.append(
        Dataset.slm_profile_id == dataset_profile_id
        if dataset_profile_id is not None
        else Dataset.slm_profile_id.is_(None)
    )
    latest = int(
        await session.scalar(select(func.max(Dataset.version)).where(*version_filter)) or 0
    )
    dataset = Dataset(
        workspace_id=workspace.id,
        slm_profile_id=dataset_profile_id,
        kind=kind,
        version=latest + 1,
        status="review_required",
        generation_config={
            "source": "manual_import",
            "idempotency_key": idempotency_key,
            "request_fingerprint": fingerprint,
        },
        example_count=len(prepared),
    )
    session.add(dataset)
    await session.flush()
    for item in prepared:
        session.add(
            DatasetExample(
                dataset_id=dataset.id,
                external_id=item["external_id"],
                split=item["split"],
                input=item["input"],
                output=item["output"],
                example_metadata=item["metadata"],
                embedding=item["embedding"],
                approved=False,
            )
        )
    await session.commit()
    return {
        "id": str(dataset.id),
        "kind": dataset.kind,
        "version": dataset.version,
        "status": dataset.status,
        "exampleCount": dataset.example_count,
    }


@router.get("/datasets/{dataset_id}")
async def dataset_detail(
    dataset_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise EcoRouteError("Dataset not found", status_code=404, code="not_found")
    distributions = list(
        (
            await session.execute(
                select(
                    DatasetExample.split,
                    func.count(DatasetExample.id),
                )
                .where(DatasetExample.dataset_id == dataset_id)
                .group_by(DatasetExample.split)
            )
        ).all()
    )
    review_counts = list(
        (
            await session.execute(
                select(
                    DatasetExample.approved,
                    DatasetExample.example_metadata["review"].astext,
                    func.count(DatasetExample.id),
                )
                .where(DatasetExample.dataset_id == dataset_id)
                .group_by(
                    DatasetExample.approved,
                    DatasetExample.example_metadata["review"].astext,
                )
            )
        ).all()
    )
    reviews = {"approved": 0, "rejected": 0, "pending": 0}
    for approved, review, count in review_counts:
        state = "approved" if approved else review if review in reviews else "pending"
        reviews[state] += int(count)
    return {
        "id": str(dataset.id),
        "kind": dataset.kind,
        "version": dataset.version,
        "status": dataset.status,
        "exampleCount": dataset.example_count,
        "manifestSha256": dataset.manifest_sha256,
        "generationConfig": dataset.generation_config,
        "distribution": {split: count for split, count in distributions},
        "reviews": reviews,
        "approvedAt": dataset.approved_at.isoformat() if dataset.approved_at else None,
        "createdAt": dataset.created_at.isoformat(),
        "updatedAt": dataset.updated_at.isoformat(),
    }


@router.get("/datasets/{dataset_id}/examples")
async def dataset_examples(
    dataset_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    split: str | None = Query(None, pattern="^(train|eval|test)$"),
    review: str | None = Query(None, pattern="^(approved|rejected|pending)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if await session.get(Dataset, dataset_id) is None:
        raise EcoRouteError("Dataset not found", status_code=404, code="not_found")
    filters: list[Any] = [DatasetExample.dataset_id == dataset_id]
    if cursor:
        filters.append(DatasetExample.external_id > cursor)
    if split:
        filters.append(DatasetExample.split == split)
    if review == "approved":
        filters.append(DatasetExample.approved.is_(True))
    elif review:
        filters.append(DatasetExample.approved.is_(False))
        filters.append(DatasetExample.example_metadata["review"].astext == review)
    items = list(
        (
            await session.scalars(
                select(DatasetExample)
                .where(*filters)
                .order_by(DatasetExample.external_id)
                .limit(limit + 1)
            )
        ).all()
    )
    next_cursor = items[limit - 1].external_id if len(items) > limit else None
    items = items[:limit]
    return {
        "items": [
            {
                "id": str(item.id),
                "externalId": item.external_id,
                "split": item.split,
                "input": item.input,
                "output": item.output,
                "metadata": item.example_metadata,
                "approved": item.approved,
            }
            for item in items
        ],
        "nextCursor": next_cursor,
    }


def _dataset_manifest(examples: list[DatasetExample]) -> str:
    canonical = [
        {
            "external_id": item.external_id,
            "split": item.split,
            "input": item.input,
            "output": item.output,
            "metadata": item.example_metadata,
        }
        for item in sorted(examples, key=lambda value: value.external_id)
    ]
    return hashlib.sha256(
        "\n".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) for row in canonical
        ).encode()
    ).hexdigest()


@router.patch("/datasets/{dataset_id}/examples/{example_id}")
async def patch_dataset_example(
    dataset_id: uuid.UUID,
    example_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    dataset = await session.get(Dataset, dataset_id)
    example = await session.get(DatasetExample, example_id)
    if dataset is None or example is None or example.dataset_id != dataset_id:
        raise EcoRouteError("Dataset example not found", status_code=404, code="not_found")
    target_dataset = dataset
    target_example = example
    if dataset.status == "approved":
        latest = int(
            await session.scalar(
                select(func.max(Dataset.version)).where(
                    Dataset.kind == dataset.kind,
                    Dataset.slm_profile_id == dataset.slm_profile_id,
                )
            )
            or dataset.version
        )
        target_dataset = Dataset(
            workspace_id=dataset.workspace_id,
            slm_profile_id=dataset.slm_profile_id,
            kind=dataset.kind,
            version=latest + 1,
            status="review_required",
            generation_config={**dataset.generation_config, "derived_from": str(dataset.id)},
            example_count=dataset.example_count,
        )
        session.add(target_dataset)
        await session.flush()
        source_examples = list(
            (
                await session.scalars(
                    select(DatasetExample).where(DatasetExample.dataset_id == dataset_id)
                )
            ).all()
        )
        for source in source_examples:
            clone = DatasetExample(
                dataset_id=target_dataset.id,
                external_id=source.external_id,
                split=source.split,
                input=source.input,
                output=source.output,
                example_metadata={**source.example_metadata, "approved": False},
                embedding=source.embedding,
                approved=False,
            )
            session.add(clone)
            if source.id == example_id:
                target_example = clone
    if target_dataset.status not in {"draft", "review_required"}:
        raise EcoRouteError(
            "Dataset is not editable",
            status_code=409,
            code="invalid_state_transition",
        )
    if "input" in body:
        input_value = " ".join(str(body["input"]).split())
        if not 3 <= len(input_value) <= 4000:
            raise EcoRouteError("input must be 3-4000 characters", code="invalid_example")
        target_example.input = input_value
        target_example.embedding = get_local_embedder(
            settings.embedding_model, settings.use_sentence_transformers
        ).encode(input_value)
        target_example.approved = False
        target_example.example_metadata = {
            **target_example.example_metadata,
            "review": "pending",
        }
    if "output" in body:
        if not isinstance(body["output"], dict):
            raise EcoRouteError("output must be an object", code="invalid_example")
        target_example.output = body["output"]
        target_example.approved = False
        target_example.example_metadata = {
            **target_example.example_metadata,
            "review": "pending",
        }
    if "metadata" in body:
        if not isinstance(body["metadata"], dict):
            raise EcoRouteError("metadata must be an object", code="invalid_example")
        target_example.example_metadata = body["metadata"]
    if "split" in body:
        if body["split"] not in {"train", "eval", "test"}:
            raise EcoRouteError("split must be train, eval, or test", code="invalid_example")
        target_example.split = body["split"]
    if "review" in body:
        if body["review"] not in {"approved", "rejected", "pending"}:
            raise EcoRouteError("Invalid review state", code="invalid_example")
        target_example.approved = body["review"] == "approved"
        target_example.example_metadata = {
            **target_example.example_metadata,
            "review": body["review"],
        }
    target_dataset.manifest_sha256 = None
    await session.commit()
    return {
        "datasetId": str(target_dataset.id),
        "createdNewVersion": target_dataset.id != dataset.id,
        "version": target_dataset.version,
        "example": {
            "id": str(target_example.id),
            "externalId": target_example.external_id,
            "split": target_example.split,
            "input": target_example.input,
            "output": target_example.output,
            "metadata": target_example.example_metadata,
            "approved": target_example.approved,
        },
    }


@router.post("/datasets/{dataset_id}/approve")
async def approve_dataset(
    dataset_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if not body.get("confirm"):
        raise EcoRouteError("Dataset approval requires confirmation", code="confirmation_required")
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None or dataset.status not in {"review_required", "draft"}:
        raise EcoRouteError(
            "Dataset must be in review_required state",
            status_code=409,
            code="invalid_state_transition",
        )
    examples = list(
        (
            await session.scalars(
                select(DatasetExample).where(DatasetExample.dataset_id == dataset_id)
            )
        ).all()
    )
    if not examples:
        raise EcoRouteError("An empty dataset cannot be approved", code="empty_dataset")
    accepted: list[DatasetExample] = []
    rejected_count = 0
    for example in examples:
        review = example.example_metadata.get("review", "pending")
        if review == "rejected":
            example.approved = False
            rejected_count += 1
            continue
        example.approved = True
        example.example_metadata = {**example.example_metadata, "review": "approved"}
        accepted.append(example)
    if not accepted:
        raise EcoRouteError(
            "At least one non-rejected example is required",
            code="empty_approved_dataset",
        )
    dataset.status = "approved"
    dataset.approved_at = utcnow()
    dataset.example_count = len(accepted)
    dataset.manifest_sha256 = _dataset_manifest(accepted)
    await session.commit()
    return {
        "id": str(dataset.id),
        "status": dataset.status,
        "manifestSha256": dataset.manifest_sha256,
        "approvedExamples": len(accepted),
        "rejectedExamples": rejected_count,
    }


@router.get("/datasets/{dataset_id}/export")
async def export_dataset(
    dataset_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None or dataset.status != "approved":
        raise EcoRouteError(
            "Only approved datasets can be exported",
            status_code=409,
            code="invalid_state_transition",
        )
    examples = list(
        (
            await session.scalars(
                select(DatasetExample)
                .where(DatasetExample.dataset_id == dataset_id)
                .where(DatasetExample.approved.is_(True))
            )
        ).all()
    )
    content = "".join(
        json.dumps(
            {
                "input": item.input,
                "output": json.dumps(item.output, separators=(",", ":")),
                "metadata": item.example_metadata,
            },
            separators=(",", ":"),
        )
        + "\n"
        for item in examples
    )
    return PlainTextResponse(content, media_type="application/x-ndjson")


@router.get("/training-runs")
async def training_runs(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cursor_id = _uuid_cursor(cursor, "training run")
    statement = select(TrainingRun).order_by(TrainingRun.id.desc()).limit(limit + 1)
    if cursor_id is not None:
        statement = statement.where(TrainingRun.id < cursor_id)
    items = list((await session.scalars(statement)).all())
    items, next_cursor = _uuid_page(items, limit)
    return {"items": [_training_json(item) for item in items], "nextCursor": next_cursor}


@router.post("/training-runs", status_code=201)
async def create_training_run(
    body: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    workspace = await _workspace(session)
    try:
        dataset_id = uuid.UUID(body["datasetId"])
    except (KeyError, ValueError) as exc:
        raise EcoRouteError("An approved datasetId is required", code="invalid_dataset") from exc
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise EcoRouteError("Dataset not found", status_code=404, code="not_found")
    if dataset.status != "approved":
        raise EcoRouteError(
            "Dataset must be approved before training",
            status_code=409,
            code="invalid_state_transition",
        )
    kind = str(body.get("kind", dataset.kind))
    if kind not in {"router", "support_slm"} or kind != dataset.kind:
        raise EcoRouteError(
            "Training kind must match the approved dataset kind", code="invalid_training_kind"
        )
    algorithm = str(body.get("algorithm", "sft"))
    allowed_algorithms = {"router": {"sft", "grpo"}, "support_slm": {"sft", "opd"}}
    if algorithm not in allowed_algorithms[kind]:
        raise EcoRouteError("Unsupported training algorithm", code="invalid_algorithm")
    locked_base_model = "Qwen/Qwen3.5-2B" if kind == "router" else "Qwen/Qwen3.5-4B"
    requested_base_model = str(body.get("baseModel", locked_base_model)).strip()
    if requested_base_model != locked_base_model:
        raise EcoRouteError(
            f"{kind} uses the locked base model {locked_base_model}", code="invalid_base_model"
        )
    template_path = (
        PROJECT_ROOT
        / "training"
        / ("router" if kind == "router" else "support-slm")
        / "configs"
        / f"{algorithm}.toml"
    )
    if not template_path.exists():
        raise EcoRouteError(
            "Training configuration template is unavailable",
            status_code=503,
            code="training_template_unavailable",
        )
    rendered = template_path.read_text()
    parent_run: TrainingRun | None = None
    if algorithm in {"grpo", "opd"}:
        try:
            parent_id = uuid.UUID(str(body["parentRunId"]))
        except (KeyError, ValueError) as exc:
            raise EcoRouteError(
                f"{algorithm} requires a completed parentRunId", code="invalid_parent_run"
            ) from exc
        parent_run = await session.get(TrainingRun, parent_id)
        if (
            parent_run is None
            or parent_run.kind != kind
            or parent_run.algorithm != "sft"
            or parent_run.status not in {"completed", "deployed", "exported"}
            or not _evaluation_gates(parent_run.kind, parent_run.eval_metrics or {})["passed"]
            or not parent_run.freesolo_run_id
        ):
            raise EcoRouteError(
                "The parent SFT run must be completed and pass evaluation gates",
                status_code=409,
                code="invalid_parent_run",
            )
        placeholder = "${ROUTER_SFT_RUN_ID}" if kind == "router" else "${SUPPORT_SFT_RUN_ID}"
        rendered = rendered.replace(placeholder, parent_run.freesolo_run_id)
    run = TrainingRun(
        workspace_id=workspace.id,
        dataset_id=dataset.id,
        slm_profile_id=dataset.slm_profile_id,
        kind=kind,
        algorithm=algorithm,
        base_model=locked_base_model,
        status="approved",
        parent_run_id=parent_run.id if parent_run else None,
        rendered_config=rendered,
    )
    session.add(run)
    await session.flush()
    await _append_training_event(
        session,
        run,
        "created",
        {"status": "approved", "datasetManifest": dataset.manifest_sha256},
    )
    await session.commit()
    return _training_json(run)


@router.get("/training-runs/{run_id}")
async def get_training_run(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    run = await session.get(TrainingRun, run_id)
    if run is None:
        raise EcoRouteError("Training run not found", status_code=404, code="not_found")
    result = _training_json(run)
    result["quoteId"] = (
        hashlib.sha256(f"{run.id}:{run.cost_quote_usd}".encode()).hexdigest()[:32]
        if run.cost_quote_usd is not None
        else None
    )
    result["freesoloConfigured"] = bool(settings.freesolo_api_key)
    result["evaluationGates"] = _evaluation_gates(run.kind, run.eval_metrics or {})
    return result


@router.get("/training-runs/{run_id}/logs")
async def training_logs(
    run_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: int | None = Query(None, ge=1),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if await session.get(TrainingRun, run_id) is None:
        raise EcoRouteError("Training run not found", status_code=404, code="not_found")
    statement = (
        select(TrainingRunEvent)
        .where(TrainingRunEvent.training_run_id == run_id)
        .order_by(TrainingRunEvent.sequence.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        statement = statement.where(TrainingRunEvent.sequence < cursor)
    events = list((await session.scalars(statement)).all())
    has_more = len(events) > limit
    events = events[:limit]
    next_cursor = str(events[-1].sequence) if has_more and events else None
    events.reverse()
    return {
        "items": [
            {
                "sequence": event.sequence,
                "type": event.event_type,
                "payload": event.payload,
                "createdAt": event.created_at.isoformat(),
            }
            for event in events
        ],
        "nextCursor": next_cursor,
    }


def _evaluation_gates(kind: str, metrics: dict[str, Any]) -> dict[str, Any]:
    if kind == "router":
        checks = {
            "schemaValidity": float(metrics.get("schema_validity", 0)) >= 0.99,
            "complexityMacroF1": float(metrics.get("complexity_macro_f1", 0)) >= 0.85,
            "riskMacroF1": float(metrics.get("risk_macro_f1", 0)) >= 0.92,
            "highRiskFalseLow": float(metrics.get("high_risk_false_low_rate", 1)) <= 0.02,
            "slmEligibilityPrecision": float(metrics.get("slm_eligibility_precision", 0)) >= 0.95,
            "latencyRecorded": metrics.get("median_latency_ms") is not None,
        }
    else:
        checks = {
            "schemaValidity": float(metrics.get("schema_validity", 0)) >= 0.99,
            "policyAccuracy": float(metrics.get("policy_accuracy", 0)) >= 0.90,
            "humanEscalationRecall": float(metrics.get("human_escalation_recall", 0)) >= 0.95,
            "prohibitedPromiseRate": float(metrics.get("prohibited_promise_rate", 1)) == 0,
            "aggregateScore": float(metrics.get("aggregate_score", 0)) >= 0.85,
        }
    return {"passed": all(checks.values()), "checks": checks}


@router.post("/training-runs/{run_id}/launch", status_code=202)
async def launch_training_run(
    run_id: uuid.UUID,
    body: dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    run = await session.get(TrainingRun, run_id)
    if run is None:
        raise EcoRouteError("Training run not found", status_code=404, code="not_found")
    confirm = bool(body.get("confirm", False))
    if not confirm:
        if run.status not in {"approved", "validating", "queued"}:
            raise EcoRouteError(
                "Run cannot be validated from its current state",
                status_code=409,
                code="invalid_state_transition",
            )
        if run.status == "approved":
            run.status = "validating"
            await _append_training_event(session, run, "status", {"status": "validating"})
            await session.commit()
        job = await _enqueue_job(
            session,
            redis,
            workspace_id=run.workspace_id,
            kind="training.validate",
            idempotency_key=idempotency_key,
            payload={"training_run_id": str(run.id)},
        )
        return {
            "runId": str(run.id),
            "status": run.status,
            "jobId": str(job.id),
            "quotePending": run.cost_quote_usd is None,
            "freesoloConfigured": bool(settings.freesolo_api_key),
        }
    if run.status != "queued" or run.cost_quote_usd is None:
        raise EcoRouteError(
            "A current validation and quote are required before launch",
            status_code=409,
            code="invalid_state_transition",
        )
    quote_id = hashlib.sha256(f"{run.id}:{run.cost_quote_usd}".encode()).hexdigest()[:32]
    if body.get("quoteId") != quote_id:
        raise EcoRouteError(
            "The quote is missing or no longer current", status_code=409, code="stale_quote"
        )
    run.status = "training"
    await _append_training_event(
        session,
        run,
        "status",
        {"status": "training", "confirmedQuoteId": quote_id},
    )
    await session.commit()
    job = await _enqueue_job(
        session,
        redis,
        workspace_id=run.workspace_id,
        kind="training.launch",
        idempotency_key=idempotency_key,
        payload={"training_run_id": str(run.id), "quote_id": quote_id},
    )
    return {"runId": str(run.id), "status": run.status, "jobId": str(job.id)}


async def _enqueue_training_action(
    *,
    run: TrainingRun,
    kind: str,
    target_status: str,
    idempotency_key: str,
    session: AsyncSession,
    redis: Redis,
    payload: dict[str, Any] | None = None,
) -> Job:
    run.status = target_status
    await _append_training_event(session, run, "status", {"status": target_status})
    await session.commit()
    return await _enqueue_job(
        session,
        redis,
        workspace_id=run.workspace_id,
        kind=kind,
        idempotency_key=idempotency_key,
        payload={"training_run_id": str(run.id), **(payload or {})},
    )


@router.post("/training-runs/{run_id}/cancel", status_code=202)
async def cancel_training_run(
    run_id: uuid.UUID,
    body: dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    run = await session.get(TrainingRun, run_id)
    if run is None:
        raise EcoRouteError("Training run not found", status_code=404, code="not_found")
    if not body.get("confirm"):
        raise EcoRouteError("Cancellation requires confirm=true", code="confirmation_required")
    if run.status not in {"training", "evaluating", "deploying"}:
        raise EcoRouteError(
            "Run is not cancellable", status_code=409, code="invalid_state_transition"
        )
    job = await _enqueue_training_action(
        run=run,
        kind="training.cancel",
        target_status="cancelling",
        idempotency_key=idempotency_key,
        session=session,
        redis=redis,
    )
    return {"runId": str(run.id), "status": run.status, "jobId": str(job.id)}


@router.post("/training-runs/{run_id}/evaluate", status_code=202)
async def evaluate_training_run(
    run_id: uuid.UUID,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    run = await session.get(TrainingRun, run_id)
    if run is None:
        raise EcoRouteError("Training run not found", status_code=404, code="not_found")
    if run.status not in {"training", "completed", "deployed"}:
        raise EcoRouteError(
            "Run is not ready for evaluation", status_code=409, code="invalid_state_transition"
        )
    job = await _enqueue_training_action(
        run=run,
        kind="training.evaluate",
        target_status="evaluating",
        idempotency_key=idempotency_key,
        session=session,
        redis=redis,
    )
    return {"runId": str(run.id), "status": run.status, "jobId": str(job.id)}


@router.post("/training-runs/{run_id}/deploy", status_code=202)
async def deploy_training_run(
    run_id: uuid.UUID,
    body: dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    run = await session.get(TrainingRun, run_id)
    if run is None:
        raise EcoRouteError("Training run not found", status_code=404, code="not_found")
    gates = _evaluation_gates(run.kind, run.eval_metrics or {})
    if run.status != "completed":
        raise EcoRouteError(
            "Only a completed run can deploy", status_code=409, code="invalid_state_transition"
        )
    if not gates["passed"] and not body.get("experimental"):
        raise EcoRouteError(
            "Evaluation gates did not pass", status_code=409, code="evaluation_gates_failed"
        )
    job = await _enqueue_training_action(
        run=run,
        kind="training.deploy",
        target_status="deploying",
        idempotency_key=idempotency_key,
        session=session,
        redis=redis,
        payload={
            "experimental": bool(body.get("experimental")),
            "region": str(body.get("region", "unknown"))[:100],
            "grid_zone": str(body.get("gridZone", "unknown"))[:100],
        },
    )
    return {"runId": str(run.id), "status": run.status, "jobId": str(job.id), "gates": gates}


@router.post("/training-runs/{run_id}/export", status_code=202)
async def export_training_run(
    run_id: uuid.UUID,
    body: dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    run = await session.get(TrainingRun, run_id)
    if run is None:
        raise EcoRouteError("Training run not found", status_code=404, code="not_found")
    if run.status not in {"completed", "deployed"}:
        raise EcoRouteError(
            "Only completed runs can be exported", status_code=409, code="invalid_state_transition"
        )
    repository = str(body.get("repository", "")).strip()
    if "/" not in repository or len(repository) > 200:
        raise EcoRouteError(
            "A repository in OWNER/REPOSITORY form is required", code="invalid_repository"
        )
    job = await _enqueue_job(
        session,
        redis,
        workspace_id=run.workspace_id,
        kind="training.export",
        idempotency_key=idempotency_key,
        payload={"training_run_id": str(run.id), "repository": repository},
    )
    return {"runId": str(run.id), "status": run.status, "jobId": str(job.id)}


@router.post("/training-runs/import", status_code=201)
async def import_training_run(
    body: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    workspace = await _workspace(session)
    try:
        dataset_id = uuid.UUID(str(body["datasetId"]))
    except (KeyError, ValueError) as exc:
        raise EcoRouteError("An approved datasetId is required", code="invalid_dataset") from exc
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise EcoRouteError("Dataset not found", status_code=404, code="not_found")
    if dataset.status != "approved":
        raise EcoRouteError(
            "Dataset must be approved before importing a run",
            status_code=409,
            code="invalid_state_transition",
        )
    external_id = str(body.get("freesoloRunId", "")).strip()
    locked_base_model = "Qwen/Qwen3.5-2B" if dataset.kind == "router" else "Qwen/Qwen3.5-4B"
    base_model = str(body.get("baseModel", locked_base_model)).strip()
    if not external_id or len(external_id) > 300:
        raise EcoRouteError("freesoloRunId is required", code="invalid_freesolo_run")
    if base_model != locked_base_model:
        raise EcoRouteError(
            f"{dataset.kind} uses the locked base model {locked_base_model}",
            code="invalid_base_model",
        )
    if await session.scalar(
        select(TrainingRun.id).where(TrainingRun.freesolo_run_id == external_id)
    ):
        raise EcoRouteError(
            "That FreeSOLO run has already been imported",
            status_code=409,
            code="duplicate_freesolo_run",
        )
    kind = str(body.get("kind", dataset.kind))
    if kind not in {"router", "support_slm"} or kind != dataset.kind:
        raise EcoRouteError(
            "Training kind must match the approved dataset kind",
            code="invalid_training_kind",
        )
    algorithm = str(body.get("algorithm", "sft"))
    allowed_algorithms = {"router": {"sft", "grpo"}, "support_slm": {"sft", "opd"}}
    if algorithm not in allowed_algorithms[kind]:
        raise EcoRouteError("Unsupported training algorithm", code="invalid_algorithm")
    parent_run: TrainingRun | None = None
    if algorithm in {"grpo", "opd"}:
        parent_id = _uuid_field(body.get("parentRunId"), "parentRunId")
        parent_run = await session.get(TrainingRun, parent_id)
        if (
            parent_run is None
            or parent_run.kind != kind
            or parent_run.algorithm != "sft"
            or parent_run.status not in {"completed", "deployed", "exported"}
            or not _evaluation_gates(parent_run.kind, parent_run.eval_metrics or {})["passed"]
        ):
            raise EcoRouteError(
                "The imported post-training run requires a completed SFT parent that passed gates",
                status_code=409,
                code="invalid_parent_run",
            )
    import_status = str(body.get("status", "completed"))
    if import_status not in {"completed", "deployed", "experimental"}:
        raise EcoRouteError(
            "Imported status must be completed, deployed, or experimental",
            code="invalid_training_status",
        )
    eval_metrics = body.get("evalMetrics", {})
    if not isinstance(eval_metrics, dict):
        raise EcoRouteError("evalMetrics must be an object", code="invalid_eval_metrics")
    gates = _evaluation_gates(kind, eval_metrics)
    experimental = import_status == "experimental"
    deployment_base_url = body.get("deploymentBaseUrl")
    deployed_model_id = body.get("deployedModelId")
    wants_deployment = import_status in {"deployed", "experimental"}
    if wants_deployment and (not deployment_base_url or not deployed_model_id):
        raise EcoRouteError(
            "Deployed imports require deploymentBaseUrl and deployedModelId",
            code="invalid_deployment",
        )
    if wants_deployment and not gates["passed"] and not experimental:
        raise EcoRouteError(
            "Evaluation gates did not pass; import with status=experimental to isolate the model",
            status_code=409,
            code="evaluation_gates_failed",
        )
    run = TrainingRun(
        workspace_id=workspace.id,
        dataset_id=dataset.id,
        slm_profile_id=dataset.slm_profile_id,
        kind=kind,
        algorithm=algorithm,
        base_model=base_model,
        status="deployed" if wants_deployment else "completed",
        parent_run_id=parent_run.id if parent_run else None,
        freesolo_run_id=external_id,
        rendered_config=body.get("renderedConfig", "# imported completed run"),
        deployment_base_url=deployment_base_url,
        deployed_model_id=deployed_model_id,
        eval_metrics=eval_metrics,
        completed_at=utcnow(),
    )
    session.add(run)
    await session.flush()
    endpoint: ModelEndpoint | None = None
    if wants_deployment:
        endpoint_options = body.get("endpoint", {})
        if not isinstance(endpoint_options, dict):
            raise EcoRouteError("endpoint must be an object", code="invalid_endpoint")
        validated = ModelEndpointCreate.model_validate(
            {
                "name": endpoint_options.get(
                    "name",
                    f"Imported FreeSOLO {'support SLM' if dataset.slm_profile_id else 'router'}",
                ),
                "provider": "freesolo",
                "baseUrl": deployment_base_url,
                "credentialRef": "env:FREESOLO_API_KEY",
                "physicalModel": deployed_model_id,
                "region": endpoint_options.get("region", "unknown"),
                "gridZone": endpoint_options.get("gridZone", "unknown"),
                "qualityTier": "specialized" if dataset.slm_profile_id else "small",
                "capabilities": endpoint_options.get(
                    "capabilities", ["text", "json_schema", "streaming"]
                ),
                "contextWindowTokens": endpoint_options.get("contextWindowTokens", 4096),
                "inputUsdPerMillionTokens": endpoint_options.get("inputUsdPerMillionTokens", 0),
                "outputUsdPerMillionTokens": endpoint_options.get("outputUsdPerMillionTokens", 0),
                "fixedRequestKwh": endpoint_options.get("fixedRequestKwh", 0),
                "inputKwhPer1kTokens": endpoint_options.get("inputKwhPer1kTokens", 0),
                "outputKwhPer1kTokens": endpoint_options.get("outputKwhPer1kTokens", 0),
                "energyEvidence": endpoint_options.get("energyEvidence", "estimated"),
                "latencyP50Ms": endpoint_options.get("latencyP50Ms", 0),
                "latencyP95Ms": endpoint_options.get("latencyP95Ms", 0),
                "selfHosted": endpoint_options.get("selfHosted", False),
                "slmProfileId": dataset.slm_profile_id,
                "enabled": endpoint_options.get("enabled", True),
            }
        )
        endpoint = ModelEndpoint(
            workspace_id=workspace.id,
            **validated.model_dump(exclude={"capabilities"}),
            capabilities=sorted(validated.capabilities),
            health_state="unknown",
            coefficient_version=str(endpoint_options.get("coefficientVersion", "imported-v1"))[
                :100
            ],
        )
        session.add(endpoint)
        await session.flush()
        if dataset.slm_profile_id:
            profile = await session.get(SlmProfile, dataset.slm_profile_id)
            if profile is not None:
                profile.active_model_endpoint_id = endpoint.id
                profile.status = "experimental" if experimental else "ready"
    await _append_training_event(
        session,
        run,
        "imported",
        {
            "status": run.status,
            "experimental": experimental,
            "evaluationGates": gates,
            "endpointId": str(endpoint.id) if endpoint else None,
        },
    )
    await session.commit()
    return {
        "id": str(run.id),
        "status": run.status,
        "experimental": experimental,
        "freesoloRunId": run.freesolo_run_id,
        "endpointId": str(endpoint.id) if endpoint else None,
        "evaluationGates": gates,
    }


@router.get("/carbon/zones")
async def carbon_zones(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    scenario = await _redis_text(redis, "ecoroute:demo:grid", "moderate")
    provider = FixtureCarbonProvider(scenario)
    fixture = [await provider.reading("demo-local"), await provider.reading("demo-remote")]
    latest_rows = list(
        (
            await session.scalars(
                select(CarbonReadingRecord)
                .distinct(CarbonReadingRecord.zone)
                .order_by(CarbonReadingRecord.zone, CarbonReadingRecord.fetched_at.desc())
            )
        ).all()
    )
    by_zone: dict[str, CarbonReadingRecord] = {}
    for row in latest_rows:
        by_zone.setdefault(row.zone, row)
    items = [
        {
            "zone": row.zone,
            "intensityGco2Kwh": row.intensity_gco2_kwh,
            "observedAt": row.observed_at.isoformat(),
            "fetchedAt": row.fetched_at.isoformat(),
            "source": row.source,
            "evidence": row.evidence,
            "freshnessSeconds": max(0, int((utcnow() - row.fetched_at).total_seconds())),
        }
        for row in by_zone.values()
    ]
    known = {item["zone"] for item in items}
    items.extend(
        {
            **reading.model_dump(mode="json", by_alias=True),
            "freshnessSeconds": max(0, int((utcnow() - reading.fetched_at).total_seconds())),
        }
        for reading in fixture
        if reading.zone not in known
    )
    items.sort(key=lambda item: str(item["zone"]))
    if cursor is not None:
        items = [item for item in items if str(item["zone"]) > cursor]
    has_more = len(items) > limit
    items = items[:limit]
    return {
        "items": items,
        "nextCursor": str(items[-1]["zone"]) if has_more and items else None,
    }


@router.post("/carbon/refresh", status_code=202)
async def refresh_carbon(
    body: dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    workspace = await _workspace(session)
    zones = body.get("zones", ["demo-local", "demo-remote"])
    if not isinstance(zones, list) or not zones or len(zones) > 50:
        raise EcoRouteError(
            "zones must be a non-empty list of at most 50 zones", code="invalid_zones"
        )
    job = await _enqueue_job(
        session,
        redis,
        workspace_id=workspace.id,
        kind="carbon.refresh",
        idempotency_key=idempotency_key,
        payload={"zones": [str(zone)[:100] for zone in zones]},
    )
    return {"jobId": str(job.id), "status": job.status}


@router.post("/demo/grid-scenario")
async def set_grid_scenario(
    body: dict[str, Any],
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if not settings.demo_mode:
        raise EcoRouteError("Not found", status_code=404, code="not_found")
    scenario = body.get("scenario")
    if scenario not in {"clean", "moderate", "dirty"}:
        raise EcoRouteError("Invalid grid scenario", code="invalid_scenario")
    await redis.set("ecoroute:demo:grid", scenario)
    workspace = await _workspace(session)
    await publish_event(redis, settings, workspace.id, "carbon.updated", {"scenario": scenario})
    return {"scenario": scenario, "evidence": "simulated"}


@router.post("/demo/quality-failure")
async def force_quality_failure(
    body: dict[str, Any], redis: Redis = Depends(get_redis)
) -> dict[str, Any]:
    if not settings.demo_mode:
        raise EcoRouteError("Not found", status_code=404, code="not_found")
    enabled = bool(body.get("enabled", True))
    if enabled:
        await redis.set("ecoroute:demo:quality-failure", "1", ex=600)
    else:
        await redis.delete("ecoroute:demo:quality-failure")
    return {"enabled": enabled}


@router.post("/demo/load", status_code=202)
async def start_demo_load(
    body: dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    if not settings.demo_mode:
        raise EcoRouteError("Not found", status_code=404, code="not_found")
    workspace = await _workspace(session)
    request_count = int(body.get("requestCount", 20))
    concurrency = int(body.get("concurrency", 2))
    if not 1 <= request_count <= 500 or not 1 <= concurrency <= 20:
        raise EcoRouteError("Demo load exceeds bounded limits", code="invalid_load")
    job = await _enqueue_job(
        session,
        redis,
        workspace_id=workspace.id,
        kind="demo.load",
        idempotency_key=idempotency_key,
        payload={
            "request_count": request_count,
            "concurrency": concurrency,
            "model": str(body.get("model", "support-default"))[:120],
            "benchmark_id": body.get("benchmarkId"),
        },
    )
    return {"jobId": str(job.id), "status": job.status}


@router.get("/agents")
async def list_agents(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cursor_id = _uuid_cursor(cursor, "agent")
    statement = select(NodeAgent).order_by(NodeAgent.id.desc()).limit(limit + 1)
    if cursor_id is not None:
        statement = statement.where(NodeAgent.id < cursor_id)
    items = list((await session.scalars(statement)).all())
    items, next_cursor = _uuid_page(items, limit)
    offline_before = utcnow() - timedelta(seconds=15)
    changed = False
    for item in items:
        if (
            item.last_heartbeat_at
            and item.last_heartbeat_at < offline_before
            and item.status != "offline"
        ):
            item.status = "offline"
            changed = True
    if changed:
        await session.commit()
    return {
        "items": [
            {
                "id": str(item.id),
                "hostname": item.hostname,
                "platform": item.platform,
                "capabilities": item.capabilities,
                "desiredProfile": item.desired_profile,
                "activeProfile": item.active_profile,
                "status": item.status,
                "lastHeartbeatAt": item.last_heartbeat_at.isoformat()
                if item.last_heartbeat_at
                else None,
                "evidence": "simulated" if item.capabilities.get("simulator") else "measured",
            }
            for item in items
        ],
        "nextCursor": next_cursor,
    }


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    agent = await session.get(NodeAgent, agent_id)
    if agent is None:
        raise EcoRouteError("Agent not found", status_code=404, code="not_found")
    samples = list(
        (
            await session.scalars(
                select(TelemetrySample)
                .where(TelemetrySample.agent_id == agent_id)
                .order_by(TelemetrySample.sequence.desc())
                .limit(300)
            )
        ).all()
    )
    events = list(
        (
            await session.scalars(
                select(OptimizationEvent)
                .where(OptimizationEvent.agent_id == agent_id)
                .order_by(OptimizationEvent.created_at.desc())
                .limit(100)
            )
        ).all()
    )
    return {
        "id": str(agent.id),
        "hostname": agent.hostname,
        "agentVersion": agent.agent_version,
        "platform": agent.platform,
        "kernelVersion": agent.kernel_version,
        "capabilities": agent.capabilities,
        "approvedControls": _approved_agent_controls(agent),
        "desiredProfile": agent.desired_profile,
        "activeProfile": agent.active_profile,
        "desiredStateVersion": agent.desired_state_version,
        "lastAppliedStateVersion": agent.last_applied_state_version,
        "status": agent.status,
        "lastHeartbeatAt": agent.last_heartbeat_at.isoformat() if agent.last_heartbeat_at else None,
        "evidence": "simulated" if agent.capabilities.get("simulator") else "measured",
        "telemetry": [sample.payload for sample in reversed(samples)],
        "events": [
            {
                "id": str(event.id),
                "desiredStateVersion": event.desired_state_version,
                "control": event.control,
                "action": event.action,
                "status": event.status,
                "plan": event.plan,
                "result": event.result,
                "createdAt": event.created_at.isoformat(),
            }
            for event in events
        ],
    }


@router.post("/agents/{agent_id}/desired-profile")
async def desired_profile(
    agent_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    agent = await session.get(NodeAgent, agent_id)
    profile = body.get("profile")
    if agent is None:
        raise EcoRouteError("Agent not found", status_code=404, code="not_found")
    if profile not in {"off", "observe", "balanced", "eco"}:
        raise EcoRouteError("Invalid profile", code="invalid_profile")
    approved = set(_approved_agent_controls(agent))
    if (
        profile in {"balanced", "eco"}
        and "nvml_power_limit" in approved
        and not body.get("confirmPowerLimit")
    ):
        raise EcoRouteError(
            "GPU power-limit changes require confirmPowerLimit=true",
            code="confirmation_required",
        )
    if (
        profile == "eco"
        and approved.intersection({"sched_ext", "napi_netdev_genl"})
        and not body.get("confirmExperimental")
    ):
        raise EcoRouteError(
            "Experimental controls require confirmExperimental=true",
            code="confirmation_required",
        )
    agent.desired_profile = profile
    agent.desired_state_version += 1
    await session.commit()
    await publish_event(
        redis,
        settings,
        agent.workspace_id,
        "agent.profile",
        {
            "agentId": str(agent.id),
            "desiredProfile": profile,
            "desiredStateVersion": agent.desired_state_version,
        },
    )
    return {"desiredProfile": profile, "desiredStateVersion": agent.desired_state_version}


def _benchmark_json(item: Benchmark) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "agentId": str(item.agent_id),
        "endpointId": str(item.endpoint_id),
        "status": item.status,
        "profile": item.profile,
        "promptSetHash": item.prompt_set_hash,
        "configuration": item.configuration,
        "baselineMetrics": item.baseline_metrics,
        "optimizedMetrics": item.optimized_metrics,
        "comparison": item.comparison,
        "evidence": item.evidence,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
        "completedAt": item.completed_at.isoformat() if item.completed_at else None,
    }


@router.get("/benchmarks")
async def list_benchmarks(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cursor_id = _uuid_cursor(cursor, "benchmark")
    statement = select(Benchmark).order_by(Benchmark.id.desc()).limit(limit + 1)
    if cursor_id is not None:
        statement = statement.where(Benchmark.id < cursor_id)
    items = list((await session.scalars(statement)).all())
    items, next_cursor = _uuid_page(items, limit)
    return {"items": [_benchmark_json(item) for item in items], "nextCursor": next_cursor}


@router.post("/benchmarks", status_code=202)
async def create_benchmark(
    body: dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    workspace = await _workspace(session)
    try:
        agent_id = uuid.UUID(body["agentId"])
        endpoint_id = uuid.UUID(body["endpointId"])
    except (KeyError, ValueError) as exc:
        raise EcoRouteError(
            "agentId and endpointId are required", code="invalid_benchmark"
        ) from exc
    agent = await session.get(NodeAgent, agent_id)
    endpoint = await session.get(ModelEndpoint, endpoint_id)
    if agent is None or endpoint is None:
        raise EcoRouteError("Agent or endpoint not found", status_code=404, code="not_found")
    if not agent.capabilities.get("simulator") and endpoint.node_agent_id != agent.id:
        raise EcoRouteError(
            "Real benchmarks must target an endpoint attached to that node agent",
            code="benchmark_endpoint_mismatch",
        )
    profile = str(body.get("profile", "eco"))
    if profile not in {"balanced", "eco"}:
        raise EcoRouteError("Benchmark profile must be balanced or eco", code="invalid_profile")
    prompt_ids = body.get("promptIds") or ["returns", "shipping", "exchange", "delay"]
    if (
        not isinstance(prompt_ids, list)
        or not prompt_ids
        or not set(prompt_ids).issubset({"returns", "shipping", "exchange", "delay"})
    ):
        raise EcoRouteError("Invalid benchmark prompt set", code="invalid_benchmark_prompt")
    configuration = {
        "warmupSeconds": min(
            max(int(body.get("warmupSeconds", 30 if settings.demo_mode else 60)), 1), 300
        ),
        "phaseSeconds": min(
            max(int(body.get("phaseSeconds", 30 if settings.demo_mode else 180)), 5), 900
        ),
        "cooldownSeconds": min(
            max(int(body.get("cooldownSeconds", 30 if settings.demo_mode else 60)), 1), 300
        ),
        "concurrency": min(max(int(body.get("concurrency", 2)), 1), 20),
        "outputTokenCap": min(max(int(body.get("outputTokenCap", 128)), 1), 2048),
        "gridScenario": str(body.get("gridScenario", "moderate")),
        "promptIds": prompt_ids,
    }
    client_request = {
        "agent_id": str(agent_id),
        "endpoint_id": str(endpoint_id),
        "profile": profile,
        "configuration": configuration,
    }
    existing_job = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing_job is not None:
        if existing_job.kind != "demo.load" or existing_job.input.get(
            "client_request_fingerprint"
        ) != _body_fingerprint(client_request):
            raise EcoRouteError(
                "Idempotency-Key was already used with a different request",
                status_code=409,
                code="idempotency_conflict",
            )
        existing_benchmark = await session.get(
            Benchmark, _uuid_field(existing_job.input.get("benchmark_id"), "benchmarkId")
        )
        if existing_benchmark is None:
            raise EcoRouteError(
                "Idempotent benchmark resource is unavailable",
                status_code=409,
                code="idempotency_resource_unavailable",
            )
        return {**_benchmark_json(existing_benchmark), "jobId": str(existing_job.id)}
    prompt_hash = hashlib.sha256(
        json.dumps(prompt_ids, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    evidence = "simulated" if agent.capabilities.get("simulator") else "measured"
    benchmark = Benchmark(
        workspace_id=workspace.id,
        agent_id=agent_id,
        endpoint_id=endpoint_id,
        status="queued",
        profile=profile,
        prompt_set_hash=prompt_hash,
        configuration=configuration,
        evidence=evidence,
    )
    session.add(benchmark)
    await session.commit()
    job = await _enqueue_job(
        session,
        redis,
        workspace_id=workspace.id,
        kind="demo.load",
        idempotency_key=idempotency_key,
        payload={
            "benchmark_id": str(benchmark.id),
            "client_request_fingerprint": _body_fingerprint(client_request),
        },
    )
    return {**_benchmark_json(benchmark), "jobId": str(job.id)}


@router.get("/benchmarks/{benchmark_id}")
async def get_benchmark(
    benchmark_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    benchmark = await session.get(Benchmark, benchmark_id)
    if benchmark is None:
        raise EcoRouteError("Benchmark not found", status_code=404, code="not_found")
    return _benchmark_json(benchmark)


@router.post("/benchmarks/{benchmark_id}/cancel", status_code=202)
async def cancel_benchmark(
    benchmark_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    benchmark = await session.get(Benchmark, benchmark_id)
    if benchmark is None:
        raise EcoRouteError("Benchmark not found", status_code=404, code="not_found")
    if not body.get("confirm"):
        raise EcoRouteError(
            "Benchmark cancellation requires confirmation", code="confirmation_required"
        )
    if benchmark.status in {"completed", "cancelled", "failed"}:
        raise EcoRouteError(
            "Benchmark is already terminal", status_code=409, code="invalid_state_transition"
        )
    benchmark.status = "cancelled"
    benchmark.completed_at = utcnow()
    jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.kind == "demo.load",
                    Job.input["benchmark_id"].astext == str(benchmark.id),
                    Job.status.in_(["queued", "running"]),
                )
            )
        ).all()
    )
    for job in jobs:
        job.status = "cancelled"
        job.completed_at = utcnow()
    agent = await session.get(NodeAgent, benchmark.agent_id)
    if agent is not None:
        agent.desired_profile = "observe"
        agent.desired_state_version += 1
    await session.commit()
    await publish_event(
        redis,
        settings,
        benchmark.workspace_id,
        "benchmark.status",
        {"benchmarkId": str(benchmark.id), "status": "cancelled"},
    )
    return _benchmark_json(benchmark)


def _report_statement(
    *,
    from_time: datetime | None,
    to_time: datetime | None,
    logical_model_id: uuid.UUID | None,
    endpoint_id: uuid.UUID | None,
    route: str | None,
    evidence: str | None,
) -> Any:
    statement = (
        select(GatewayRequest, ModelEndpoint, ImpactRecord)
        .outerjoin(ModelEndpoint, GatewayRequest.selected_endpoint_id == ModelEndpoint.id)
        .outerjoin(
            ImpactRecord,
            and_(
                ImpactRecord.request_id == GatewayRequest.id,
                ImpactRecord.strategy == "end_to_end",
            ),
        )
    )
    if from_time:
        statement = statement.where(GatewayRequest.started_at >= from_time)
    if to_time:
        statement = statement.where(GatewayRequest.started_at <= to_time)
    if logical_model_id:
        statement = statement.where(GatewayRequest.logical_model_id == logical_model_id)
    if endpoint_id:
        statement = statement.where(GatewayRequest.selected_endpoint_id == endpoint_id)
    if route:
        if route == "cache":
            statement = statement.where(GatewayRequest.cache_status.in_(["exact", "semantic"]))
        else:
            statement = statement.where(ModelEndpoint.name == route)
    if evidence:
        statement = statement.where(ImpactRecord.evidence["carbon_level"].astext == evidence)
    return statement.order_by(GatewayRequest.started_at)


def _report_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _report_range(
    from_time: datetime | None, to_time: datetime | None
) -> tuple[datetime | None, datetime | None]:
    normalized_from = _report_time(from_time)
    normalized_to = _report_time(to_time)
    if normalized_from and normalized_to and normalized_from > normalized_to:
        raise EcoRouteError("Report start must not be after its end", code="invalid_time_range")
    return normalized_from, normalized_to


async def _report_rows(
    session: AsyncSession,
    *,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    logical_model_id: uuid.UUID | None = None,
    endpoint_id: uuid.UUID | None = None,
    route: str | None = None,
    evidence: str | None = None,
) -> list[tuple[GatewayRequest, ModelEndpoint | None, ImpactRecord | None]]:
    rows = (
        await session.execute(
            _report_statement(
                from_time=from_time,
                to_time=to_time,
                logical_model_id=logical_model_id,
                endpoint_id=endpoint_id,
                route=route,
                evidence=evidence,
            )
        )
    ).all()
    return cast(list[tuple[GatewayRequest, ModelEndpoint | None, ImpactRecord | None]], list(rows))


def _report_filter_metadata(
    from_time: datetime | None,
    to_time: datetime | None,
    logical_model_id: uuid.UUID | None,
    endpoint_id: uuid.UUID | None,
    route: str | None,
    evidence: str | None,
) -> dict[str, Any]:
    return {
        "from": from_time.isoformat() if from_time else None,
        "to": to_time.isoformat() if to_time else None,
        "logicalModelId": str(logical_model_id) if logical_model_id else None,
        "endpointId": str(endpoint_id) if endpoint_id else None,
        "route": route,
        "evidence": evidence,
    }


@router.get("/reports/summary")
async def report_summary(
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    logical_model_id: uuid.UUID | None = Query(None, alias="logicalModelId"),
    endpoint_id: uuid.UUID | None = Query(None, alias="endpointId"),
    route: str | None = None,
    evidence: str | None = Query(None, pattern="^(measured|estimated|stale|simulated)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from_time, to_time = _report_range(from_time, to_time)
    rows = await _report_rows(
        session,
        from_time=from_time,
        to_time=to_time,
        logical_model_id=logical_model_id,
        endpoint_id=endpoint_id,
        route=route,
        evidence=evidence,
    )
    impacts = [impact for _, _, impact in rows if impact is not None]
    evidence_counts = {"measured": 0, "estimated": 0, "stale": 0, "simulated": 0}
    for impact in impacts:
        level = str(impact.evidence.get("carbon_level", "estimated"))
        evidence_counts[level] = evidence_counts.get(level, 0) + 1
    baseline_carbon = sum(item.baseline_carbon_g for item in impacts)
    actual_carbon = sum(item.actual_carbon_g for item in impacts)
    baseline_energy = sum(item.baseline_energy_kwh for item in impacts)
    actual_energy = sum(item.actual_energy_kwh for item in impacts)
    baseline_cost = sum(float(item.baseline_cost_usd) for item in impacts)
    actual_cost = sum(float(item.actual_cost_usd) for item in impacts)
    durations = [item.duration_ms for item, _, _ in rows if item.duration_ms is not None]
    return {
        "generatedAt": utcnow().isoformat(),
        "filters": _report_filter_metadata(
            from_time, to_time, logical_model_id, endpoint_id, route, evidence
        ),
        "requestCount": len(rows),
        "successfulRequests": sum(item.status == "completed" for item, _, _ in rows),
        "qualityFallbacks": sum(item.fallback_used for item, _, _ in rows),
        "averageLatencyMs": sum(durations) / len(durations) if durations else 0,
        "baselineCarbonGrams": baseline_carbon,
        "actualCarbonGrams": actual_carbon,
        "rawCarbonDeltaGrams": baseline_carbon - actual_carbon,
        "avoidedCarbonGrams": sum(max(0, item.raw_carbon_delta_g) for item in impacts),
        "carbonOutcome": "increase" if actual_carbon > baseline_carbon else "avoided",
        "baselineEnergyKwh": baseline_energy,
        "actualEnergyKwh": actual_energy,
        "baselineCostUsd": baseline_cost,
        "actualCostUsd": actual_cost,
        "costDeltaUsd": actual_cost - baseline_cost,
        "evidenceCounts": evidence_counts,
        "operationalCarbonIntensityLabel": "Operational carbon intensity",
        "methodologyVersion": "ecoroute-v1",
        "boundary": "Operational inference energy and carbon; embodied carbon excluded.",
    }


@router.get("/reports/requests.csv")
async def request_csv(
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    logical_model_id: uuid.UUID | None = Query(None, alias="logicalModelId"),
    endpoint_id: uuid.UUID | None = Query(None, alias="endpointId"),
    route: str | None = None,
    evidence: str | None = Query(None, pattern="^(measured|estimated|stale|simulated)$"),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    from_time, to_time = _report_range(from_time, to_time)
    rows = await _report_rows(
        session,
        from_time=from_time,
        to_time=to_time,
        logical_model_id=logical_model_id,
        endpoint_id=endpoint_id,
        route=route,
        evidence=evidence,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "request_id",
            "started_at_utc",
            "logical_model",
            "status",
            "route",
            "endpoint_id",
            "cache",
            "fallback",
            "duration_ms",
            "baseline_cost_usd",
            "actual_cost_usd",
            "baseline_energy_kwh",
            "actual_energy_kwh",
            "baseline_carbon_g",
            "actual_carbon_g",
            "raw_carbon_delta_g",
            "evidence",
            "carbon_source",
            "redacted_prompt_preview",
        ]
    )
    for item, endpoint, impact in rows:
        route_name = (
            "cache"
            if item.cache_status in {"exact", "semantic"}
            else endpoint.name
            if endpoint
            else "unknown"
        )
        writer.writerow(
            [
                item.id,
                item.started_at.isoformat(),
                item.requested_model_alias,
                item.status,
                route_name,
                item.selected_endpoint_id,
                item.cache_status,
                item.fallback_used,
                item.duration_ms,
                impact.baseline_cost_usd if impact else "",
                impact.actual_cost_usd if impact else "",
                impact.baseline_energy_kwh if impact else "",
                impact.actual_energy_kwh if impact else "",
                impact.baseline_carbon_g if impact else "",
                impact.actual_carbon_g if impact else "",
                impact.raw_carbon_delta_g if impact else "",
                impact.evidence.get("carbon_level", "") if impact else "",
                impact.evidence.get("carbon_source", "") if impact else "",
                item.redacted_prompt_preview or "",
            ]
        )
    generated = utcnow().isoformat()
    filters = _report_filter_metadata(
        from_time, to_time, logical_model_id, endpoint_id, route, evidence
    )
    evidence_counts = {"measured": 0, "estimated": 0, "stale": 0, "simulated": 0}
    for _, _, impact in rows:
        if impact is not None:
            level = str(impact.evidence.get("carbon_level", "estimated"))
            evidence_counts[level] = evidence_counts.get(level, 0) + 1
    return PlainTextResponse(
        output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="ecoroute-requests.csv"',
            "X-EcoRoute-Generated-At": generated,
            "X-EcoRoute-Methodology-Version": "ecoroute-v1",
            "X-EcoRoute-Filters": json.dumps(filters, separators=(",", ":")),
            "X-EcoRoute-Evidence-Counts": json.dumps(evidence_counts, separators=(",", ":")),
        },
    )


@router.post("/reports/impact-framework")
async def impact_framework(
    body: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    def parse_time(value: Any) -> datetime | None:
        if value in {None, ""}:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise EcoRouteError("Invalid report timestamp", code="invalid_time_range") from exc

    try:
        logical_model_id = (
            uuid.UUID(str(body["logicalModelId"])) if body.get("logicalModelId") else None
        )
        endpoint_id = uuid.UUID(str(body["endpointId"])) if body.get("endpointId") else None
    except ValueError as exc:
        raise EcoRouteError("Invalid report identifier", code="invalid_report_filter") from exc
    from_time = parse_time(body.get("from"))
    to_time = parse_time(body.get("to"))
    from_time, to_time = _report_range(from_time, to_time)
    route = str(body["route"]) if body.get("route") else None
    evidence = str(body["evidence"]) if body.get("evidence") else None
    if evidence and evidence not in {"measured", "estimated", "stale", "simulated"}:
        raise EcoRouteError("Invalid evidence filter", code="invalid_report_filter")
    rows = await _report_rows(
        session,
        from_time=from_time,
        to_time=to_time,
        logical_model_id=logical_model_id,
        endpoint_id=endpoint_id,
        route=route,
        evidence=evidence,
    )
    grouped: dict[tuple[str, datetime, str, str, str], dict[str, Any]] = {}
    evidence_counts = {"measured": 0, "estimated": 0, "stale": 0, "simulated": 0}
    for request, endpoint, record in rows:
        if record is None:
            continue
        hour = request.started_at.replace(minute=0, second=0, microsecond=0)
        endpoint_name = (
            "cache"
            if request.cache_status in {"exact", "semantic"}
            else endpoint.name
            if endpoint
            else "unknown"
        )
        level = str(record.evidence.get("carbon_level", "estimated"))
        source = str(record.evidence.get("carbon_source", "unknown"))
        coefficient_version = str(record.evidence.get("coefficient_version", "unknown"))
        key = (endpoint_name, hour, level, source, coefficient_version)
        group = grouped.setdefault(
            key,
            {
                "timestamp": hour.isoformat(),
                "duration": 0.0,
                "energy": 0.0,
                "carbon": 0.0,
                "requests": 0,
                "grid-intensity-gco2-kwh": 0.0,
                "evidence": level,
                "source": source,
                "coefficient-version": coefficient_version,
                "attribution-method": record.evidence.get("attribution_method", "unknown"),
            },
        )
        group["duration"] += (request.duration_ms or 0) / 1000
        group["energy"] += record.actual_energy_kwh
        group["carbon"] += record.actual_carbon_g
        group["requests"] += 1
        evidence_counts[level] = evidence_counts.get(level, 0) + 1
    for item in grouped.values():
        item["grid-intensity-gco2-kwh"] = item["carbon"] / item["energy"] if item["energy"] else 0.0
    children: dict[str, Any] = {}
    for (endpoint_name, _, _, _, _), item in grouped.items():
        slug = "".join(
            character if character.isalnum() else "-" for character in endpoint_name.lower()
        )
        child = children.setdefault(
            slug,
            {
                "pipeline": [],
                "defaults": {"methodology-version": "ecoroute-v1", "endpoint": endpoint_name},
                "inputs": [],
            },
        )
        child["inputs"].append(item)
    generated = utcnow().isoformat()
    filters = _report_filter_metadata(
        from_time, to_time, logical_model_id, endpoint_id, route, evidence
    )
    manifest = {
        "name": "EcoRoute operational impact export",
        "description": "Precomputed operational observations; simulated values remain explicitly labeled.",
        "tags": {"kind": "web", "methodology-version": "ecoroute-v1"},
        "metadata": {
            "generated-at": generated,
            "filters": filters,
            "evidence-counts": evidence_counts,
            "boundary": "Operational inference energy and carbon; embodied carbon excluded.",
            "note": "Energy and carbon values are precomputed EcoRoute observations; the empty pipeline preserves an auditable rerunnable manifest.",
        },
        "initialize": {"plugins": {}},
        "tree": {"children": children},
    }
    return PlainTextResponse(
        yaml.safe_dump(manifest, sort_keys=False),
        media_type="application/yaml",
        headers={
            "Content-Disposition": 'attachment; filename="ecoroute-impact.yml"',
            "X-EcoRoute-Generated-At": generated,
            "X-EcoRoute-Methodology-Version": "ecoroute-v1",
        },
    )


@agent_router.post("/agents/register")
async def register_agent(
    body: AgentRegistration,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    workspace = await _workspace(session)
    agent = await session.get(NodeAgent, body.agent_id)
    capabilities = body.capabilities.model_dump()
    if agent is None:
        agent = NodeAgent(
            id=body.agent_id,
            workspace_id=workspace.id,
            hostname=body.hostname,
            agent_version=body.agent_version,
            platform=body.platform,
            kernel_version=body.kernel_version,
            capabilities=capabilities,
            desired_profile="observe",
            active_profile="observe",
            status="online",
            last_heartbeat_at=utcnow(),
        )
        session.add(agent)
    else:
        agent.capabilities = capabilities
        agent.status = "online"
        agent.last_heartbeat_at = utcnow()
    await session.commit()
    await publish_event(
        redis,
        settings,
        workspace.id,
        "agent.heartbeat",
        {"agentId": str(agent.id), "status": "registered"},
    )
    return {
        "desiredProfile": agent.desired_profile,
        "approvedControls": _approved_agent_controls(agent),
        "telemetryIntervalSeconds": 1,
        "desiredStateVersion": agent.desired_state_version,
    }


@agent_router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(
    agent_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    agent = await session.get(NodeAgent, agent_id)
    if agent is None:
        raise EcoRouteError("Agent not registered", status_code=404, code="not_found")
    agent.status = "online"
    agent.last_heartbeat_at = utcnow()
    agent.active_profile = body.get("activeProfile", agent.active_profile)
    agent.last_applied_state_version = int(
        body.get("lastAppliedStateVersion", agent.last_applied_state_version)
    )
    endpoint_ids = list(
        (
            await session.scalars(
                select(ModelEndpoint.id).where(ModelEndpoint.node_agent_id == agent_id)
            )
        ).all()
    )
    recent = list(
        (
            await session.scalars(
                select(GatewayRequest).where(
                    GatewayRequest.selected_endpoint_id.in_(endpoint_ids),
                    GatewayRequest.started_at >= utcnow() - timedelta(seconds=10),
                )
            )
        ).all()
    )
    durations = sorted(item.duration_ms for item in recent if item.duration_ms is not None)
    p95_index = max(0, int(len(durations) * 0.95) - 1)
    current_metrics = {
        "p95LatencyMs": durations[p95_index] if durations else None,
        "errorRate": sum(item.status != "completed" for item in recent) / len(recent)
        if recent
        else None,
        "throughputRps": len(recent) / 10,
    }
    baseline = await session.scalar(
        select(Benchmark)
        .where(Benchmark.agent_id == agent_id, Benchmark.status == "completed")
        .order_by(Benchmark.completed_at.desc())
        .limit(1)
    )
    benchmark = await session.scalar(
        select(Benchmark)
        .where(
            Benchmark.agent_id == agent_id,
            Benchmark.status.in_(["assigned", "running"]),
            Benchmark.evidence == "measured",
        )
        .order_by(Benchmark.created_at)
        .limit(1)
    )
    await session.commit()
    await publish_event(
        redis,
        settings,
        agent.workspace_id,
        "agent.heartbeat",
        {"agentId": str(agent.id), "activeProfile": agent.active_profile},
    )
    return {
        "desiredProfile": agent.desired_profile,
        "desiredStateVersion": agent.desired_state_version,
        "approvedControls": _approved_agent_controls(agent),
        "guardrails": {
            "current": current_metrics,
            "baseline": baseline.baseline_metrics if baseline else None,
        },
        "benchmark": (
            {
                "id": str(benchmark.id),
                "profile": benchmark.profile,
                "status": benchmark.status,
                "configuration": benchmark.configuration,
                "promptSetHash": benchmark.prompt_set_hash,
            }
            if benchmark
            else None
        ),
    }


@agent_router.post("/agents/{agent_id}/benchmarks/{benchmark_id}/sample")
async def agent_benchmark_sample(
    agent_id: uuid.UUID,
    benchmark_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    benchmark = await session.get(Benchmark, benchmark_id)
    if benchmark is None or benchmark.agent_id != agent_id:
        raise EcoRouteError("Benchmark not found", status_code=404, code="not_found")
    if benchmark.status not in {"assigned", "running"}:
        raise EcoRouteError(
            "Benchmark is not accepting samples",
            status_code=409,
            code="invalid_state_transition",
        )
    endpoint = await session.get(ModelEndpoint, benchmark.endpoint_id)
    if endpoint is None or endpoint.node_agent_id != agent_id:
        raise EcoRouteError("Benchmark endpoint is unavailable", code="benchmark_endpoint_mismatch")
    prompts = {
        "returns": "What is the return window for an unused item?",
        "shipping": "Summarize the standard shipping policy.",
        "exchange": "Can I exchange an item if the replacement is out of stock?",
        "delay": "My shipment has not moved for eight business days. What should I do?",
    }
    prompt_id = str(body.get("promptId", "returns"))
    prompt = prompts.get(prompt_id)
    if prompt is None:
        raise EcoRouteError("Unknown benchmark prompt ID", code="invalid_benchmark_prompt")
    request = ChatCompletionRequest(
        model=endpoint.physical_model,
        messages=[ChatMessage(role="user", content=prompt)],
        temperature=0,
        max_tokens=int(benchmark.configuration.get("outputTokenCap", 128)),
    )
    started = time.monotonic()
    try:
        completion = await providers.for_provider(endpoint.provider).chat(endpoint, request)
    except Exception as exc:
        return {
            "success": False,
            "latencyMs": int((time.monotonic() - started) * 1000),
            "inputTokens": 0,
            "outputTokens": 0,
            "qualityScore": 0,
            "errorCode": type(exc).__name__,
        }
    usage = completion.get("usage") or {}
    choices = completion.get("choices") or []
    content = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
    if benchmark.status == "assigned":
        benchmark.status = "running"
        await session.commit()
    return {
        "success": bool(content),
        "latencyMs": int((time.monotonic() - started) * 1000),
        "inputTokens": int(usage.get("prompt_tokens", 0)),
        "outputTokens": int(usage.get("completion_tokens", 0)),
        "qualityScore": 1.0 if content else 0.0,
    }


@agent_router.post("/agents/{agent_id}/telemetry", status_code=202)
async def agent_telemetry(
    agent_id: uuid.UUID,
    body: list[TelemetryPayload],
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    agent = await session.get(NodeAgent, agent_id)
    if agent is None:
        raise EcoRouteError("Agent not registered", status_code=404, code="not_found")
    if not body or len(body) > 60:
        raise EcoRouteError(
            "Telemetry batches must contain between 1 and 60 samples",
            code="invalid_telemetry_batch",
        )
    if agent.capabilities.get("simulator") and any(item.evidence != "simulated" for item in body):
        raise EcoRouteError(
            "Simulator telemetry must be labeled simulated",
            code="invalid_evidence",
            status_code=422,
        )
    accepted = 0
    for item in body:
        if item.agent_id != agent_id:
            raise EcoRouteError("Agent ID mismatch", code="agent_id_mismatch")
        existing = await session.get(TelemetrySample, (agent_id, item.sequence))
        if existing is None:
            session.add(
                TelemetrySample(
                    agent_id=agent_id,
                    sequence=item.sequence,
                    observed_at=item.observed_at,
                    profile=item.profile,
                    payload=item.model_dump(mode="json"),
                    evidence=item.evidence,
                )
            )
            accepted += 1
            metrics.AGENT_OPTIMIZATION.labels(str(agent_id), item.profile).set(
                1 if item.profile in {"balanced", "eco"} else 0
            )
            for device in item.gpu:
                if device.get("power_watts") is not None:
                    metrics.AGENT_POWER.labels(
                        str(agent_id), str(device.get("uuid", "unknown")), item.evidence
                    ).set(float(device["power_watts"]))
    await session.commit()
    if accepted:
        await publish_event(
            redis,
            settings,
            agent.workspace_id,
            "agent.telemetry",
            {"agentId": str(agent_id), "accepted": accepted},
        )
    return {"accepted": accepted}


@agent_router.post("/agents/{agent_id}/events", status_code=202)
async def agent_event(
    agent_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    event_agent = await session.get(NodeAgent, agent_id)
    if event_agent is None:
        raise EcoRouteError("Agent not registered", status_code=404, code="not_found")
    benchmark_completed: Benchmark | None = None
    if body.get("control") == "benchmark" and body.get("status") == "completed":
        result = body.get("result") or {}
        try:
            benchmark_id = uuid.UUID(str(result["benchmarkId"]))
        except (KeyError, ValueError) as exc:
            raise EcoRouteError(
                "Benchmark result requires benchmarkId", code="invalid_benchmark_result"
            ) from exc
        benchmark_completed = await session.get(Benchmark, benchmark_id)
        if benchmark_completed is None or benchmark_completed.agent_id != agent_id:
            raise EcoRouteError("Benchmark not found", status_code=404, code="not_found")
        if benchmark_completed.status not in {"assigned", "running"}:
            raise EcoRouteError(
                "Benchmark is not accepting results",
                status_code=409,
                code="invalid_state_transition",
            )
        baseline = result.get("baseline")
        optimized = result.get("optimized")
        if not isinstance(baseline, dict) or not isinstance(optimized, dict):
            raise EcoRouteError(
                "Benchmark result requires baseline and optimized metrics",
                code="invalid_benchmark_result",
            )
        expected_evidence = "simulated" if event_agent.capabilities.get("simulator") else "measured"
        if result.get("evidence") != expected_evidence:
            raise EcoRouteError(
                f"Benchmark evidence must be labeled {expected_evidence}",
                code="invalid_evidence",
                status_code=422,
            )
        required = {
            "successful_throughput_rps",
            "p50_latency_ms",
            "p95_latency_ms",
            "energy_per_request_kwh",
            "energy_per_token_kwh",
            "quality_score",
        }
        if not required.issubset(baseline) or not required.issubset(optimized):
            raise EcoRouteError("Benchmark metrics are incomplete", code="invalid_benchmark_result")

        def percentage(before: Any, after: Any) -> float | None:
            initial = float(before or 0)
            return round((float(after or 0) / initial - 1) * 100, 3) if initial else None

        benchmark_completed.baseline_metrics = baseline
        benchmark_completed.optimized_metrics = optimized
        benchmark_completed.comparison = {
            "throughputChangePct": percentage(
                baseline["successful_throughput_rps"],
                optimized["successful_throughput_rps"],
            ),
            "p95LatencyChangePct": percentage(
                baseline["p95_latency_ms"], optimized["p95_latency_ms"]
            ),
            "energyPerRequestChangePct": percentage(
                baseline["energy_per_request_kwh"],
                optimized["energy_per_request_kwh"],
            ),
            "qualityChange": round(
                float(optimized["quality_score"]) - float(baseline["quality_score"]), 4
            ),
        }
        benchmark_completed.status = "completed"
        benchmark_completed.completed_at = utcnow()
        benchmark_completed.evidence = expected_evidence
        benchmark_agent = await session.get(NodeAgent, agent_id)
        if benchmark_agent is not None:
            benchmark_agent.desired_profile = "observe"
            benchmark_agent.active_profile = "observe"
            benchmark_agent.desired_state_version += 1
    if body.get("control") == "gateway_concurrency" and body.get("status") == "completed":
        target = int((body.get("result") or {}).get("target", 0))
        if target < 1:
            raise EcoRouteError("Invalid concurrency target", code="invalid_control_result")
        endpoints = list(
            (
                await session.scalars(
                    select(ModelEndpoint).where(ModelEndpoint.node_agent_id == agent_id)
                )
            ).all()
        )
        for endpoint in endpoints:
            endpoint.concurrency_target = min(target, endpoint.baseline_concurrency)
            endpoint.version += 1
    session.add(
        OptimizationEvent(
            agent_id=agent_id,
            desired_state_version=int(body["desiredStateVersion"]),
            control=body.get("control", "profile"),
            action=body.get("action", "apply"),
            status=body.get("status", "completed"),
            snapshot=body.get("snapshot"),
            plan=body.get("plan"),
            result=body.get("result"),
        )
    )
    await session.commit()
    agent = await session.get(NodeAgent, agent_id)
    if agent is not None:
        await publish_event(
            redis,
            settings,
            agent.workspace_id,
            "agent.rollback" if "rollback" in str(body.get("action")) else "agent.profile",
            {
                "agentId": str(agent_id),
                "control": body.get("control", "profile"),
                "action": body.get("action", "apply"),
                "status": body.get("status", "completed"),
            },
        )
        if benchmark_completed is not None:
            await publish_event(
                redis,
                settings,
                agent.workspace_id,
                "benchmark.status",
                {
                    "benchmarkId": str(benchmark_completed.id),
                    "status": "completed",
                    "comparison": benchmark_completed.comparison,
                },
            )
    return {"accepted": True}
