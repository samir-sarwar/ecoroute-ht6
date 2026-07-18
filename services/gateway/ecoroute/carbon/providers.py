from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from ecoroute.api.schemas import CarbonReading
from ecoroute.config import Settings

FIXTURES = {"clean": 100.0, "moderate": 275.0, "dirty": 650.0}


class CarbonProvider(Protocol):
    async def reading(
        self,
        zone: str,
        *,
        data_center_provider: str | None = None,
        data_center_region: str | None = None,
    ) -> CarbonReading: ...


def _utc_timestamp(value: Any, *, default: datetime) -> datetime:
    if not isinstance(value, str) or not value:
        return default
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class FixtureCarbonProvider:
    def __init__(self, scenario: str = "moderate") -> None:
        self.scenario = scenario

    async def reading(
        self,
        zone: str,
        *,
        data_center_provider: str | None = None,
        data_center_region: str | None = None,
    ) -> CarbonReading:
        now = datetime.now(timezone.utc)
        intensity = 80.0 if zone == "demo-remote" else FIXTURES.get(self.scenario, 275.0)
        return CarbonReading(
            zone=zone,
            intensity_gco2_kwh=intensity,
            observed_at=now,
            fetched_at=now,
            source=f"ecoroute-fixture:{self.scenario}",
            evidence="simulated",
            metadata={
                "lookup_mode": "data_center" if data_center_provider else "zone",
                "data_center_provider": data_center_provider,
                "data_center_region": data_center_region,
                "is_estimated": True,
            },
        )


class CarbonAwareSdkProvider:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def reading(
        self,
        zone: str,
        *,
        data_center_provider: str | None = None,
        data_center_region: str | None = None,
    ) -> CarbonReading:
        if data_center_provider or data_center_region:
            raise ValueError("Carbon Aware does not support data-center region lookup")
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{self.base_url}/emissions/bylocation", params={"location": zone}
            )
            response.raise_for_status()
        values = response.json()
        item = values[0] if isinstance(values, list) else values
        now = datetime.now(timezone.utc)
        observed = item.get("time") or item.get("timestamp")
        return CarbonReading(
            zone=zone,
            intensity_gco2_kwh=float(item["rating"]),
            observed_at=_utc_timestamp(observed, default=now),
            fetched_at=now,
            source=str(item.get("source", "carbon-aware-sdk")),
            evidence="estimated",
            metadata={
                "lookup_mode": "zone",
                "emission_factor_type": "provider_default",
            },
        )


class ElectricityMapsProvider:
    """Timestamped, fail-closed Electricity Maps v4 carbon-intensity adapter."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.electricitymaps.com/v4",
        *,
        timeout_seconds: float = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Electricity Maps API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def reading(
        self,
        zone: str,
        *,
        data_center_provider: str | None = None,
        data_center_region: str | None = None,
    ) -> CarbonReading:
        if bool(data_center_provider) != bool(data_center_region):
            raise ValueError("Both data-center provider and region are required")
        params: dict[str, str] = {
            "disableCallerLookup": "true",
            "emissionFactorType": "lifecycle",
            "flowTraced": "true",
            "temporalGranularity": "5_minutes",
        }
        if data_center_provider and data_center_region:
            params.update(
                {
                    "dataCenterProvider": data_center_provider.casefold(),
                    "dataCenterRegion": data_center_region.casefold().replace("_", "-"),
                }
            )
            lookup_mode = "data_center"
        else:
            params["zone"] = zone
            lookup_mode = "zone"
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, transport=self.transport
        ) as client:
            response = await client.get(
                f"{self.base_url}/carbon-intensity/latest",
                params=params,
                headers={"auth-token": self.api_key},
            )
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("Electricity Maps returned a non-object response")
        nested = payload.get("data")
        item: Mapping[str, Any] = nested if isinstance(nested, Mapping) else payload
        raw_intensity = item.get("carbonIntensity", item.get("carbon_intensity"))
        if isinstance(raw_intensity, Mapping):
            raw_intensity = raw_intensity.get("value")
        try:
            intensity = float(raw_intensity)
        except (TypeError, ValueError) as exc:
            raise ValueError("Electricity Maps response omitted carbon intensity") from exc
        if not math.isfinite(intensity) or intensity < 0:
            raise ValueError("Electricity Maps returned an invalid carbon intensity")
        returned_zone = item.get("zone")
        if not isinstance(returned_zone, str) or not returned_zone:
            raise ValueError("Electricity Maps response did not identify its grid zone")
        if returned_zone.casefold() != zone.casefold():
            raise ValueError(f"Electricity Maps resolved zone {returned_zone!r}; expected {zone!r}")
        now = datetime.now(timezone.utc)
        observed = _utc_timestamp(
            item.get("datetime", item.get("timestamp")),
            default=now,
        )
        is_estimated = item.get("isEstimated", item.get("is_estimated"))
        estimation_method = item.get("estimationMethod", item.get("estimation_method"))
        return CarbonReading(
            zone=returned_zone,
            intensity_gco2_kwh=intensity,
            observed_at=observed,
            fetched_at=now,
            source=f"electricity-maps:v4:lifecycle:flow-traced:{lookup_mode}",
            evidence="estimated" if is_estimated is not False else "measured",
            metadata={
                "provider": "electricity_maps",
                "api_version": "v4",
                "lookup_mode": lookup_mode,
                "data_center_provider": data_center_provider,
                "data_center_region": data_center_region,
                "is_estimated": is_estimated is not False,
                "estimation_method": estimation_method,
                "emission_factor_type": item.get("emissionFactorType", "lifecycle"),
                "flow_traced": True,
                "temporal_granularity": item.get("temporalGranularity", "5_minutes"),
                "updated_at": item.get("updatedAt", item.get("updated_at")),
            },
        )


def configured_carbon_provider(settings: Settings) -> CarbonProvider:
    provider = settings.carbon_provider
    if provider == "electricity_maps" or (
        provider == "auto" and bool(settings.electricity_maps_api_key)
    ):
        return ElectricityMapsProvider(
            settings.electricity_maps_api_key,
            settings.electricity_maps_base_url,
            timeout_seconds=settings.carbon_request_timeout_seconds,
        )
    if provider == "carbon_aware":
        return CarbonAwareSdkProvider(settings.carbon_aware_base_url)
    raise ValueError(
        "No live carbon provider is configured; set ELECTRICITY_MAPS_API_KEY or explicitly "
        "select ECOROUTE_CARBON_PROVIDER=carbon_aware"
    )


def configured_carbon_provider_name(settings: Settings) -> str:
    if settings.demo_mode:
        return "fixture"
    if settings.carbon_provider == "electricity_maps" or (
        settings.carbon_provider == "auto" and bool(settings.electricity_maps_api_key)
    ):
        return "electricity_maps"
    if settings.carbon_provider == "carbon_aware":
        return "carbon_aware"
    return "unconfigured"
