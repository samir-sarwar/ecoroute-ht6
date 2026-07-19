from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field("development", validation_alias="ECOROUTE_ENV")
    demo_mode: bool = Field(True, validation_alias="ECOROUTE_DEMO_MODE")
    gateway_key: str = Field("ecoroute-demo-key", validation_alias="ECOROUTE_GATEWAY_KEY")
    agent_token: str = Field("replace-me", validation_alias="ECOROUTE_AGENT_TOKEN")
    database_url: str = Field(
        "postgresql+asyncpg://ecoroute:ecoroute@localhost:5432/ecoroute",
        validation_alias="ECOROUTE_DATABASE_URL",
    )
    redis_url: str = Field("redis://localhost:6379/0", validation_alias="ECOROUTE_REDIS_URL")
    public_url: str = Field("http://localhost:8000", validation_alias="ECOROUTE_PUBLIC_URL")
    gateway_internal_url: str = Field(
        "http://gateway:8000", validation_alias="ECOROUTE_GATEWAY_INTERNAL_URL"
    )
    carbon_aware_base_url: str = Field(
        "http://carbon-aware:8080", validation_alias="CARBON_AWARE_BASE_URL"
    )
    event_stream_maxlen: int = Field(10_000, validation_alias="ECOROUTE_EVENT_STREAM_MAXLEN")
    telemetry_retention_days: int = Field(7, validation_alias="ECOROUTE_TELEMETRY_RETENTION_DAYS")
    request_retention_days: int = Field(30, validation_alias="ECOROUTE_REQUEST_RETENTION_DAYS")
    simulator_seed: int = Field(42, validation_alias="ECOROUTE_SIMULATOR_SEED")
    fake_provider_delay_ms: int = Field(35, validation_alias="ECOROUTE_FAKE_PROVIDER_DELAY_MS")
    embedding_model: str = Field(
        "sentence-transformers/all-MiniLM-L6-v2", validation_alias="ECOROUTE_EMBEDDING_MODEL"
    )
    use_sentence_transformers: bool = Field(
        True, validation_alias="ECOROUTE_USE_SENTENCE_TRANSFORMERS"
    )
    freesolo_router_base_url: str = Field("", validation_alias="FREESOLO_ROUTER_BASE_URL")
    freesolo_router_model_id: str = Field("", validation_alias="FREESOLO_ROUTER_MODEL_ID")
    freesolo_support_base_url: str = Field("", validation_alias="FREESOLO_SUPPORT_BASE_URL")
    freesolo_support_model_id: str = Field("", validation_alias="FREESOLO_SUPPORT_MODEL_ID")
    freesolo_api_key: str = Field("", validation_alias="FREESOLO_API_KEY")
    freesolo_org: str = Field("", validation_alias="FREESOLO_ORG")
    gemini_api_key: str = Field("", validation_alias="GEMINI_API_KEY")
    gemini_dataset_model: str = Field("gemini-2.5-flash", validation_alias="GEMINI_DATASET_MODEL")
    openai_api_key: str = Field("", validation_alias="OPENAI_API_KEY")
    electricity_maps_api_key: str = Field("", validation_alias="ELECTRICITY_MAPS_API_KEY")
    electricity_maps_base_url: str = Field(
        "https://api.electricitymaps.com/v4",
        validation_alias="ELECTRICITY_MAPS_BASE_URL",
    )
    carbon_provider: Literal["auto", "electricity_maps", "carbon_aware"] = Field(
        "auto", validation_alias="ECOROUTE_CARBON_PROVIDER"
    )
    ollama_base_url: str = Field(
        "http://host.docker.internal:11434", validation_alias="OLLAMA_BASE_URL"
    )
    vllm_base_url: str = Field("", validation_alias="VLLM_BASE_URL")
    hf_token: str = Field("", validation_alias="HF_TOKEN")
    hf_router_repository: str = Field("", validation_alias="HF_ROUTER_REPOSITORY")
    hf_support_repository: str = Field("", validation_alias="HF_SUPPORT_REPOSITORY")
    support_demo_gateway_key: str = Field(
        "ecoroute-demo-key", validation_alias="ECOROUTE_SUPPORT_DEMO_GATEWAY_KEY"
    )
    provider_timeout_seconds: int = Field(45, validation_alias="ECOROUTE_PROVIDER_TIMEOUT_SECONDS")
    stream_timeout_seconds: int = Field(120, validation_alias="ECOROUTE_STREAM_TIMEOUT_SECONDS")
    carbon_cache_seconds: int = Field(300, validation_alias="ECOROUTE_CARBON_CACHE_SECONDS")
    carbon_request_timeout_seconds: float = Field(
        5.0, validation_alias="ECOROUTE_CARBON_REQUEST_TIMEOUT_SECONDS", gt=0, le=30
    )
    carbon_freshness_target_minutes: int = Field(
        15, validation_alias="ECOROUTE_CARBON_FRESHNESS_TARGET_MINUTES", ge=1, le=60
    )
    cache_max_entries: int = Field(10_000, validation_alias="ECOROUTE_CACHE_MAX_ENTRIES")
    cache_lookup_kwh: float = Field(0.000001, validation_alias="ECOROUTE_CACHE_LOOKUP_KWH")
    max_request_body_bytes: int = Field(
        2_000_000, validation_alias="ECOROUTE_MAX_REQUEST_BODY_BYTES"
    )
    max_sse_connections: int = Field(100, validation_alias="ECOROUTE_MAX_SSE_CONNECTIONS")
    allowed_endpoint_hosts: str = Field("", validation_alias="ECOROUTE_ALLOWED_ENDPOINT_HOSTS")
    allowed_credential_envs: str = Field(
        "FREESOLO_API_KEY,GEMINI_API_KEY,OPENAI_API_KEY,OPENAI_US_API_KEY,"
        "OPENAI_EU_API_KEY,AZURE_OPENAI_CANADA_KEY,AZURE_OPENAI_SWEDEN_KEY,"
        "AZURE_OPENAI_DEMO_KEY,OLLAMA_API_KEY,VLLM_API_KEY",
        validation_alias="ECOROUTE_ALLOWED_CREDENTIAL_ENVS",
    )
    agent_approved_controls: str = Field(
        "gateway_concurrency", validation_alias="ECOROUTE_AGENT_APPROVED_CONTROLS"
    )

    @property
    def carbon_scenario(self) -> Literal["clean", "moderate", "dirty"]:
        return "moderate"


@lru_cache
def get_settings() -> Settings:
    return Settings()
