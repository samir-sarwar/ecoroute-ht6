from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ecoroute.db.base import Base, utcnow, uuid7


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    store_prompt_content: Mapped[bool] = mapped_column(Boolean, default=False)


class LogicalModel(Base, TimestampMixin):
    __tablename__ = "logical_models"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    alias: Mapped[str] = mapped_column(String(120), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    baseline_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    impact_baseline_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    required_fallback_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    active_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("workspace_id", "alias"),)


class ModelEndpoint(Base, TimestampMixin):
    __tablename__ = "model_endpoints"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(40))
    base_url: Mapped[str] = mapped_column(Text)
    credential_ref: Mapped[str | None] = mapped_column(String(200))
    physical_model: Mapped[str] = mapped_column(String(300))
    azure_deployment_type: Mapped[str | None] = mapped_column(String(40))
    region: Mapped[str] = mapped_column(String(100))
    grid_zone: Mapped[str] = mapped_column(String(100), index=True)
    grid_lookup_mode: Mapped[str] = mapped_column(String(20), default="zone")
    grid_data_center_provider: Mapped[str | None] = mapped_column(String(100))
    grid_data_center_region: Mapped[str | None] = mapped_column(String(100))
    processing_location_evidence: Mapped[str] = mapped_column(String(30), default="unknown")
    grid_attribution: Mapped[str] = mapped_column(String(40), default="unknown")
    quality_tier: Mapped[str] = mapped_column(String(40))
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    context_window_tokens: Mapped[int] = mapped_column(Integer)
    input_usd_per_million_tokens: Mapped[Decimal] = mapped_column(Numeric(18, 9))
    output_usd_per_million_tokens: Mapped[Decimal] = mapped_column(Numeric(18, 9))
    fixed_request_kwh: Mapped[float] = mapped_column(Float)
    input_kwh_per_1k_tokens: Mapped[float] = mapped_column(Float)
    output_kwh_per_1k_tokens: Mapped[float] = mapped_column(Float)
    energy_evidence: Mapped[str] = mapped_column(String(20))
    latency_p50_ms: Mapped[int] = mapped_column(Integer)
    latency_p95_ms: Mapped[int] = mapped_column(Integer)
    self_hosted: Mapped[bool] = mapped_column(Boolean, default=False)
    slm_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_state: Mapped[str] = mapped_column(String(20), default="unknown")
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_error: Mapped[str | None] = mapped_column(Text)
    coefficient_version: Mapped[str] = mapped_column(String(100), default="demo-v1")
    calibration: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    node_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    baseline_concurrency: Mapped[int] = mapped_column(Integer, default=16)
    concurrency_target: Mapped[int] = mapped_column(Integer, default=16)
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LogicalModelEndpoint(Base):
    __tablename__ = "logical_model_endpoints"
    logical_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("logical_models.id"), primary_key=True
    )
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_endpoints.id"), primary_key=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=100)


class RoutingPolicy(Base):
    __tablename__ = "routing_policies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid7)
    version_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    preset: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(100), default="demo-operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("family_id", "version_number"),)


class SlmProfile(Base, TimestampMixin):
    __tablename__ = "slm_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    business_name: Mapped[str] = mapped_column(String(200))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_version: Mapped[int] = mapped_column(Integer, default=1)
    active_model_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    slm_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slm_profiles.id"), index=True)
    policy_key: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("slm_profile_id", "policy_key", "version"),)


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    slm_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slm_profiles.id"))
    kind: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    generation_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    example_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("kind", "slm_profile_id", "version"),)


class DatasetExample(Base):
    __tablename__ = "dataset_examples"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(100))
    split: Mapped[str] = mapped_column(String(10))
    input: Mapped[str] = mapped_column(Text)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB)
    example_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("dataset_id", "external_id"),)


class TrainingRun(Base, TimestampMixin):
    __tablename__ = "training_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"))
    slm_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slm_profiles.id"))
    kind: Mapped[str] = mapped_column(String(30))
    algorithm: Mapped[str] = mapped_column(String(20))
    base_model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(40))
    freesolo_environment_id: Mapped[str | None] = mapped_column(String(300))
    freesolo_run_id: Mapped[str | None] = mapped_column(String(300), unique=True)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rendered_config: Mapped[str] = mapped_column(Text)
    cost_quote_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 9))
    eval_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    deployment_base_url: Mapped[str | None] = mapped_column(Text)
    deployed_model_id: Mapped[str | None] = mapped_column(String(300))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrainingRunEvent(Base):
    __tablename__ = "training_run_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    training_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("training_runs.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("training_run_id", "sequence"),)


class GatewayRequest(Base):
    __tablename__ = "gateway_requests"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    logical_model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("logical_models.id"))
    requested_model_alias: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), index=True)
    stream: Mapped[bool] = mapped_column(Boolean)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    request_features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    client_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    redacted_prompt_preview: Mapped[str | None] = mapped_column(Text)
    raw_prompt_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    cache_status: Mapped[str] = mapped_column(String(20))
    router_classification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    selected_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_endpoints.id"))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    first_token_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (
        Index("ix_gateway_workspace_started", "workspace_id", "started_at"),
        Index("ix_gateway_endpoint_started", "selected_endpoint_id", "started_at"),
        Index("ix_gateway_status_started", "status", "started_at"),
        Index("ix_gateway_client_metadata", "client_metadata", postgresql_using="gin"),
    )


class RouteDecision(Base):
    __tablename__ = "route_decisions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gateway_requests.id"), unique=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("routing_policies.id"))
    grid_state: Mapped[str] = mapped_column(String(20))
    candidate_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    selected_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_endpoints.id"))
    selection_reason: Mapped[str] = mapped_column(String(200))
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelAttempt(Base):
    __tablename__ = "model_attempts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gateway_requests.id"))
    attempt_number: Mapped[int] = mapped_column(SmallInteger)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_endpoints.id"))
    purpose: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(40))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    upstream_request_id: Mapped[str | None] = mapped_column(String(300))
    quality_verdict: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("request_id", "attempt_number"),)


class ImpactRecord(Base):
    __tablename__ = "impact_records"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gateway_requests.id"))
    strategy: Mapped[str] = mapped_column(String(30))
    baseline_energy_kwh: Mapped[float] = mapped_column(Float)
    actual_energy_kwh: Mapped[float] = mapped_column(Float)
    baseline_carbon_g: Mapped[float] = mapped_column(Float)
    actual_carbon_g: Mapped[float] = mapped_column(Float)
    raw_carbon_delta_g: Mapped[float] = mapped_column(Float)
    baseline_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 9))
    actual_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 9))
    carbon_accounting_available: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("request_id", "strategy"),)


class CacheEntry(Base):
    __tablename__ = "cache_entries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    logical_model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("logical_models.id"), index=True)
    exact_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    namespace_version: Mapped[int] = mapped_column(Integer)
    system_prompt_hash: Mapped[str] = mapped_column(String(64))
    tool_schema_hash: Mapped[str | None] = mapped_column(String(64))
    response_format_hash: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(20))
    normalized_semantic_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    completion: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gateway_requests.id"))
    source_endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_endpoints.id"))
    quality_verdict: Mapped[dict[str, Any]] = mapped_column(JSONB)
    baseline_energy_kwh: Mapped[float] = mapped_column(Float)
    baseline_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 9))
    hit_count: Mapped[int] = mapped_column(BigInteger, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CarbonReadingRecord(Base):
    __tablename__ = "carbon_readings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    zone: Mapped[str] = mapped_column(String(100), index=True)
    intensity_gco2_kwh: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(200))
    evidence: Mapped[str] = mapped_column(String(20))
    lookup_key: Mapped[str] = mapped_column(String(240), default="zone")
    reading_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint(
            "zone", "observed_at", "source", "lookup_key", name="uq_carbon_readings_lookup"
        ),
    )


class NodeAgent(Base, TimestampMixin):
    __tablename__ = "node_agents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"))
    hostname: Mapped[str] = mapped_column(String(200))
    agent_version: Mapped[str] = mapped_column(String(40))
    platform: Mapped[str] = mapped_column(String(50))
    kernel_version: Mapped[str | None] = mapped_column(String(100))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB)
    desired_profile: Mapped[str] = mapped_column(String(20), default="observe")
    active_profile: Mapped[str] = mapped_column(String(20), default="observe")
    desired_state_version: Mapped[int] = mapped_column(BigInteger, default=1)
    last_applied_state_version: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20), default="offline")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelemetrySample(Base):
    __tablename__ = "telemetry_samples"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node_agents.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    profile: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence: Mapped[str] = mapped_column(String(20))


class OptimizationEvent(Base):
    __tablename__ = "optimization_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node_agents.id"), index=True)
    desired_state_version: Mapped[int] = mapped_column(BigInteger)
    control: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(40))
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Benchmark(Base, TimestampMixin):
    __tablename__ = "benchmarks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node_agents.id"))
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_endpoints.id"))
    status: Mapped[str] = mapped_column(String(40))
    profile: Mapped[str] = mapped_column(String(20))
    prompt_set_hash: Mapped[str] = mapped_column(String(64))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    baseline_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    optimized_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    comparison: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence: Mapped[str] = mapped_column(String(20))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"))
    kind: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_cache_context",
    CacheEntry.workspace_id,
    CacheEntry.logical_model_id,
    CacheEntry.system_prompt_hash,
)
Index(
    "uq_cache_active_fingerprint",
    CacheEntry.workspace_id,
    CacheEntry.namespace_version,
    CacheEntry.exact_fingerprint,
    unique=True,
    postgresql_where=CacheEntry.invalidated_at.is_(None),
)
Index(
    "ix_dataset_examples_embedding_hnsw",
    DatasetExample.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
Index(
    "ix_cache_entries_embedding_hnsw",
    CacheEntry.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
