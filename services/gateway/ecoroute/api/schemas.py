from __future__ import annotations

import ipaddress
import os
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvidenceLevel = Literal["measured", "estimated", "stale", "simulated"]
ProcessingLocationEvidence = Literal[
    "provider_contract", "operator_declared", "self_hosted", "unknown", "simulated"
]
GridAttribution = Literal[
    "electricity_maps_data_center",
    "physical_grid",
    "regional_proxy",
    "operator_declared",
    "unknown",
    "simulated",
]
TaskType = Literal[
    "policy_qa",
    "order_support",
    "summarization",
    "classification",
    "extraction",
    "reply_draft",
    "tool_workflow",
    "legal",
    "safety",
    "coding",
    "general_reasoning",
    "unknown",
]


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.capitalize() for word in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_call_id: str | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            return "\n".join(
                str(part.get("text", "")) for part in self.content if part.get("type") == "text"
            )
        return ""

    def is_multimodal(self) -> bool:
        return isinstance(self.content, list) and any(
            # A missing or unknown content-part type is not safely optimizable as
            # text. Fail closed into capability passthrough routing.
            part.get("type") != "text"
            for part in self.content
        )


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = Field(min_length=1, max_length=120)
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, ge=0, le=1)
    max_tokens: int | None = Field(None, gt=0, le=32768)
    max_completion_tokens: int | None = Field(None, gt=0, le=32768)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    stop: str | list[str] | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    user: str | None = Field(None, max_length=200)
    seed: int | None = None
    metadata: dict[str, str] | None = None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return value
        allowed = {"demo_session_id", "demo_message_id", "client_app", "ecoroute_debug"}
        return {
            key: item
            for key, item in value.items()
            if key in allowed and len(key) <= 64 and isinstance(item, str) and len(item) <= 256
        }


class NormalizedRequestFeatures(BaseModel):
    request_id: uuid.UUID
    logical_model: str
    normalized_text: str
    system_prompt_hash: str
    tool_schema_hash: str | None
    response_format_hash: str | None
    message_count: int
    assistant_turn_count: int = 0
    input_token_estimate: int
    has_tools: bool
    has_multimodal: bool
    contains_pii: bool
    contains_secrets: bool
    is_personalized: bool
    deterministic: bool
    requested_language: str
    redacted_preview: str
    detection_uncertain: bool = False


class RouterClassification(BaseModel):
    complexity: Literal["low", "medium", "high"]
    task_type: TaskType
    risk: Literal["low", "medium", "high"]
    slm_eligible: bool
    cache_eligible: bool
    required_capabilities: list[Literal["text", "json_schema", "tools", "vision", "streaming"]]
    predicted_output_tokens: int = Field(ge=1, le=4096)
    confidence: float = Field(ge=0, le=1)
    rationale_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    classification_source: Literal["deterministic", "trained_adapter", "fail_closed"] = (
        "deterministic"
    )

    @classmethod
    def fail_closed(cls, rationale: str = "ROUTER_UNAVAILABLE") -> RouterClassification:
        return cls(
            complexity="high",
            task_type="unknown",
            risk="high",
            slm_eligible=False,
            cache_eligible=False,
            required_capabilities=["text"],
            predicted_output_tokens=256,
            confidence=0.0,
            rationale_code=rationale,
            classification_source="fail_closed",
        )


class RoutingWeights(ApiModel):
    carbon: float = Field(ge=0, le=1)
    cost: float = Field(ge=0, le=1)
    latency: float = Field(ge=0, le=1)
    quality: float = Field(ge=0, le=1)
    evidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_sum(self) -> RoutingWeights:
        if (
            abs(sum((self.carbon, self.cost, self.latency, self.quality, self.evidence)) - 1)
            > 0.001
        ):
            raise ValueError("routing weights must sum to 1.0")
        return self


PRESET_CONFIG: dict[str, tuple[RoutingWeights, float]] = {
    "eco": (RoutingWeights(carbon=0.45, cost=0.20, latency=0.10, quality=0.20, evidence=0.05), 0),
    "balanced": (
        RoutingWeights(carbon=0.30, cost=0.20, latency=0.20, quality=0.25, evidence=0.05),
        10,
    ),
    "strict_quality": (
        RoutingWeights(carbon=0.10, cost=0.10, latency=0.10, quality=0.65, evidence=0.05),
        0,
    ),
    "cost_saver": (
        RoutingWeights(carbon=0.15, cost=0.55, latency=0.10, quality=0.15, evidence=0.05),
        0,
    ),
}


class RoutingPolicyConfig(ApiModel):
    name: str = "Balanced"
    preset: Literal["eco", "balanced", "strict_quality", "cost_saver", "custom"] = "balanced"
    enabled_endpoint_ids: list[uuid.UUID] = Field(default_factory=list)
    min_router_confidence: float = Field(0.70, ge=0, le=1)
    min_slm_confidence: float = Field(0.80, ge=0, le=1)
    max_latency_ms: int = Field(30_000, gt=0)
    max_cost_increase_pct: float = Field(10.0, ge=0, le=100)
    clean_threshold_gco2_kwh: float = 150.0
    dirty_threshold_gco2_kwh: float = 400.0
    semantic_cache_enabled: bool = True
    semantic_cache_task_types: list[TaskType] = Field(
        default_factory=lambda: cast(list[TaskType], ["policy_qa"])
    )
    semantic_similarity_threshold: float = Field(0.94, ge=0.90, le=0.99)
    cache_ttl_seconds: int = Field(86_400, ge=60)
    quality_fallback_enabled: bool = True
    allow_experimental_models: bool = False
    allow_stale_carbon_minutes: int = Field(60, ge=0)
    allowed_regions: list[str] = Field(default_factory=list)
    sensitive_requires_self_hosted: bool = False
    weights: RoutingWeights = Field(default_factory=lambda: PRESET_CONFIG["balanced"][0])
    task_rules: list[dict[str, Any]] = Field(default_factory=list)
    namespace_version: int = 1

    @model_validator(mode="after")
    def validate_thresholds(self) -> RoutingPolicyConfig:
        if self.clean_threshold_gco2_kwh >= self.dirty_threshold_gco2_kwh:
            raise ValueError("clean threshold must be below dirty threshold")
        return self


class ModelEndpointCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    provider: Literal[
        "azure_openai",
        "freesolo",
        "gemini",
        "openai",
        "ollama",
        "vllm",
        "openai_compatible",
        "fake",
    ]
    base_url: str
    credential_ref: str | None = Field(None, max_length=200)
    physical_model: str = Field(min_length=1, max_length=300)
    azure_deployment_type: Literal[
        "standard", "provisioned_managed", "global_standard"
    ] | None = None
    region: str = Field(min_length=1, max_length=100)
    grid_zone: str = Field(min_length=1, max_length=100)
    grid_lookup_mode: Literal["zone", "data_center"] = "zone"
    grid_data_center_provider: str | None = Field(None, min_length=1, max_length=100)
    grid_data_center_region: str | None = Field(None, min_length=1, max_length=100)
    processing_location_evidence: ProcessingLocationEvidence = "unknown"
    grid_attribution: GridAttribution = "unknown"
    quality_tier: Literal["specialized", "small", "standard", "frontier"]
    capabilities: set[Literal["text", "json_schema", "tools", "vision", "streaming"]] = Field(
        min_length=1
    )
    context_window_tokens: int = Field(gt=0)
    input_usd_per_million_tokens: Decimal = Field(ge=0)
    output_usd_per_million_tokens: Decimal = Field(ge=0)
    fixed_request_kwh: Decimal = Field(ge=0)
    input_kwh_per_1k_tokens: Decimal = Field(ge=0)
    output_kwh_per_1k_tokens: Decimal = Field(ge=0)
    energy_evidence: EvidenceLevel
    latency_p50_ms: int = Field(ge=0)
    latency_p95_ms: int = Field(ge=0)
    self_hosted: bool
    node_agent_id: uuid.UUID | None = None
    slm_profile_id: uuid.UUID | None = None
    baseline_concurrency: int = Field(16, ge=1, le=10_000)
    concurrency_target: int = Field(16, ge=1, le=10_000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_endpoint(self) -> ModelEndpointCreate:
        if self.quality_tier == "specialized" and self.slm_profile_id is None:
            raise ValueError("specialized endpoints require slmProfileId")
        if self.credential_ref and not re.fullmatch(
            r"env:[A-Z][A-Z0-9_]{0,127}", self.credential_ref
        ):
            raise ValueError("credentialRef must be an env: reference using an uppercase name")
        if not self.region or not self.grid_zone:
            raise ValueError("region and gridZone are required")
        if self.grid_lookup_mode == "data_center":
            if not self.grid_data_center_provider or not self.grid_data_center_region:
                raise ValueError(
                    "data-center grid lookup requires gridDataCenterProvider and "
                    "gridDataCenterRegion"
                )
            if self.grid_attribution != "electricity_maps_data_center":
                raise ValueError(
                    "data-center grid lookup requires gridAttribution=electricity_maps_data_center"
                )
        elif self.grid_data_center_provider or self.grid_data_center_region:
            raise ValueError("data-center fields require gridLookupMode=data_center")
        if self.grid_attribution == "electricity_maps_data_center" and (
            self.grid_lookup_mode != "data_center"
        ):
            raise ValueError(
                "gridAttribution=electricity_maps_data_center requires data-center lookup"
            )
        if self.grid_attribution == "regional_proxy" and self.processing_location_evidence in {
            "unknown",
            "simulated",
        }:
            raise ValueError(
                "regional proxy attribution requires documented or operator-declared processing"
            )
        if self.processing_location_evidence == "self_hosted" and not self.self_hosted:
            raise ValueError("self-hosted location evidence requires selfHosted=true")
        if self.grid_attribution == "physical_grid" and (
            not self.self_hosted or self.processing_location_evidence != "self_hosted"
        ):
            raise ValueError(
                "physical-grid attribution requires a self-hosted endpoint with "
                "self-hosted location evidence"
            )
        if not self.self_hosted and self.energy_evidence == "measured":
            raise ValueError("hosted endpoint energy cannot be labeled measured")
        if self.provider == "openai" and self.processing_location_evidence == "provider_contract":
            hostname = (urlparse(self.base_url).hostname or "").casefold()
            region = self.region.casefold().replace("_", "-")
            expected_host = {
                "us": "us.api.openai.com",
                "usa": "us.api.openai.com",
                "eu": "eu.api.openai.com",
                "europe": "eu.api.openai.com",
            }.get(region)
            if expected_host is None or hostname != expected_host:
                raise ValueError(
                    "OpenAI provider-contract location evidence requires a matching "
                    "US or EU regional API hostname"
                )
        if self.provider == "azure_openai":
            parsed = urlparse(self.base_url)
            hostname = (parsed.hostname or "").casefold()
            if (
                parsed.scheme != "https"
                or not hostname.endswith((".openai.azure.com", ".services.ai.azure.com"))
                or parsed.path.rstrip("/") != "/openai/v1"
            ):
                raise ValueError(
                    "Azure OpenAI endpoints require an official HTTPS resource URL ending "
                    "in /openai/v1"
                )
            if self.azure_deployment_type == "global_standard":
                if self.region.casefold() != "global":
                    raise ValueError("Azure Global Standard endpoints require region=global")
                if self.grid_zone.casefold() != "unknown" or self.grid_attribution != "unknown":
                    raise ValueError(
                        "Azure Global Standard endpoints require gridZone=unknown and "
                        "gridAttribution=unknown"
                    )
            elif self.azure_deployment_type not in {"standard", "provisioned_managed"}:
                raise ValueError(
                    "Azure endpoints require Standard, regional Provisioned Managed, or "
                    "Global Standard deployment metadata"
                )
            if not self.credential_ref:
                raise ValueError("Azure OpenAI endpoints require credentialRef")
            if self.self_hosted:
                raise ValueError("Azure OpenAI endpoints cannot be marked selfHosted")
            demo_mode = os.getenv("ECOROUTE_DEMO_MODE", "false").casefold() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if self.processing_location_evidence != "provider_contract" and not (
                demo_mode and self.processing_location_evidence == "simulated"
            ):
                raise ValueError(
                    "Azure endpoints require provider-contract processing-location evidence"
                )
        elif self.azure_deployment_type is not None:
            raise ValueError("azureDeploymentType requires provider=azure_openai")
        if self.concurrency_target > self.baseline_concurrency:
            raise ValueError("concurrencyTarget cannot exceed baselineConcurrency")
        if self.node_agent_id is not None and not self.self_hosted:
            raise ValueError("nodeAgentId requires selfHosted=true")
        return self

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if len(value) > 2048:
            raise ValueError("baseUrl is too long")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("baseUrl must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("baseUrl cannot contain user info, a query, or a fragment")
        hostname = parsed.hostname.casefold()
        metadata_hosts = {
            "169.254.169.254",
            "metadata.google.internal",
            "metadata.google",
            "instance-data",
        }
        allowed = {
            host.strip().casefold()
            for host in os.getenv("ECOROUTE_ALLOWED_ENDPOINT_HOSTS", "").split(",")
            if host.strip()
        }
        if hostname in metadata_hosts and hostname not in allowed:
            raise ValueError("cloud metadata endpoint URLs are blocked")
        try:
            address = ipaddress.ip_address(parsed.hostname)
            environment = os.getenv("ECOROUTE_ENV", "development")
            blocked_private = address.is_private or address.is_loopback or address.is_reserved
            if address.is_link_local or str(address) == "169.254.169.254":
                raise ValueError("link-local and metadata endpoint URLs are blocked")
            if (
                environment not in {"development", "test"}
                and blocked_private
                and hostname not in allowed
            ):
                raise ValueError("private and loopback endpoint URLs require an explicit allowlist")
        except ValueError as exc:
            if "blocked" in str(exc) or "allowlist" in str(exc):
                raise
        return value.rstrip("/")


class CarbonReading(BaseModel):
    zone: str
    intensity_gco2_kwh: float = Field(ge=0)
    observed_at: datetime
    fetched_at: datetime
    source: str
    evidence: EvidenceLevel
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateSnapshot(BaseModel):
    endpoint_id: uuid.UUID
    name: str
    provider: str
    quality_tier: str
    estimated_energy_kwh: float
    estimated_cost_usd: Decimal
    estimated_carbon_g: float | None
    latency_p95_ms: int
    evidence: EvidenceLevel
    region: str | None = None
    azure_deployment_type: Literal[
        "standard", "provisioned_managed", "global_standard"
    ] | None = None
    grid_zone: str | None = None
    carbon_evidence: EvidenceLevel | None = None
    processing_location_evidence: ProcessingLocationEvidence = "unknown"
    grid_attribution: GridAttribution = "unknown"
    carbon_source: str | None = None
    score: float | None = None
    excluded_reason: str | None = None


class QualityVerdict(BaseModel):
    passed: bool
    reason: str
    confidence: float | None = None
    policy_ids: list[str] = Field(default_factory=list)
    answer: str | None = None


class AgentCapabilities(ApiModel):
    nvml_energy: bool = False
    nvml_power_limit: bool = False
    rapl: bool = False
    cgroups_v2: bool = False
    nice_ionice: bool = False
    sched_ext: bool = False
    napi_netdev_genl: bool = False
    simulator: bool = False


class AgentRegistration(ApiModel):
    agent_id: uuid.UUID
    hostname: str
    agent_version: str
    platform: str
    kernel_version: str | None = None
    capabilities: AgentCapabilities


class TelemetryPayload(ApiModel):
    agent_id: uuid.UUID
    sequence: int = Field(ge=0)
    observed_at: datetime
    profile: Literal["off", "observe", "balanced", "eco"]
    cpu_percent: float | None = None
    memory_percent: float | None = None
    network_rx_bytes: int | None = None
    network_tx_bytes: int | None = None
    gpu: list[dict[str, Any]] = Field(default_factory=list)
    rapl_energy_uj: int | None = None
    evidence: EvidenceLevel

    @model_validator(mode="after")
    def simulator_never_measured(self) -> TelemetryPayload:
        return self
