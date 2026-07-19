from __future__ import annotations

from datetime import timedelta

import httpx
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecoroute.api.schemas import CarbonReading
from ecoroute.carbon.providers import FixtureCarbonProvider, configured_carbon_provider
from ecoroute.config import Settings
from ecoroute.db.base import utcnow
from ecoroute.db.models import CarbonReadingRecord

GRID_OVERRIDE_KEY = "ecoroute:demo:grid_override"
GRID_OVERRIDE_INTENSITIES = {"dirty": 650.0}


def carbon_cache_key(
    zone: str,
    data_center_provider: str | None = None,
    data_center_region: str | None = None,
) -> str:
    return f"ecoroute:carbon:{zone}:{carbon_lookup_key(data_center_provider, data_center_region)}"


def carbon_lookup_key(
    data_center_provider: str | None = None,
    data_center_region: str | None = None,
) -> str:
    if data_center_provider and data_center_region:
        return (
            f"dc:{data_center_provider.casefold()}:"
            f"{data_center_region.casefold().replace('_', '-')}"
        )
    return "zone"


async def grid_override_state(redis: Redis) -> str | None:
    value = await redis.get(GRID_OVERRIDE_KEY)
    if isinstance(value, bytes):
        value = value.decode()
    if value in GRID_OVERRIDE_INTENSITIES:
        return str(value)
    if value is not None:
        await redis.delete(GRID_OVERRIDE_KEY)
    return None


class CarbonService:
    def __init__(self, settings: Settings, redis: Redis) -> None:
        self.settings = settings
        self.redis = redis

    async def reading(
        self,
        session: AsyncSession,
        zone: str,
        *,
        demo_scenario: str | None,
        allow_stale_minutes: int,
        data_center_provider: str | None = None,
        data_center_region: str | None = None,
    ) -> CarbonReading:
        if self.settings.demo_mode:
            return await FixtureCarbonProvider(demo_scenario or "moderate").reading(
                zone,
                data_center_provider=data_center_provider,
                data_center_region=data_center_region,
            )
        override = await grid_override_state(self.redis)
        if override is not None:
            now = utcnow()
            return CarbonReading(
                zone=zone,
                intensity_gco2_kwh=GRID_OVERRIDE_INTENSITIES[override],
                observed_at=now,
                fetched_at=now,
                source=f"ecoroute-grid-override:{override}",
                evidence="simulated",
                metadata={
                    "available": True,
                    "lookup_mode": "data_center" if data_center_provider else "zone",
                    "data_center_provider": data_center_provider,
                    "data_center_region": data_center_region,
                    "override": True,
                    "scenario": override,
                },
            )
        cache_key = carbon_cache_key(zone, data_center_provider, data_center_region)
        cached = await self.redis.get(cache_key)
        freshness_target = timedelta(minutes=self.settings.carbon_freshness_target_minutes)
        maximum_age = timedelta(
            minutes=max(self.settings.carbon_freshness_target_minutes, allow_stale_minutes)
        )
        if cached:
            try:
                cached_reading = CarbonReading.model_validate_json(cached)
                cached_age = max(timedelta(0), utcnow() - cached_reading.observed_at)
                if cached_age <= maximum_age:
                    return (
                        cached_reading.model_copy(update={"evidence": "stale"})
                        if cached_age > freshness_target
                        else cached_reading
                    )
                await self.redis.delete(cache_key)
            except ValidationError:
                await self.redis.delete(cache_key)
        try:
            reading = await configured_carbon_provider(self.settings).reading(
                zone,
                data_center_provider=data_center_provider,
                data_center_region=data_center_region,
            )
            age = max(timedelta(0), utcnow() - reading.observed_at)
            if age > maximum_age:
                raise ValueError("Carbon reading exceeded the policy freshness allowance")
            if age > freshness_target:
                reading = reading.model_copy(update={"evidence": "stale"})
            await self.redis.set(
                cache_key,
                reading.model_dump_json(),
                ex=self.settings.carbon_cache_seconds,
            )
            session.add(
                CarbonReadingRecord(
                    zone=reading.zone,
                    intensity_gco2_kwh=reading.intensity_gco2_kwh,
                    observed_at=reading.observed_at,
                    fetched_at=reading.fetched_at,
                    source=reading.source,
                    evidence=reading.evidence,
                    lookup_key=carbon_lookup_key(
                        data_center_provider,
                        data_center_region,
                    ),
                    reading_metadata=reading.metadata,
                )
            )
            return reading
        except (TimeoutError, TypeError, OSError, ValueError, httpx.HTTPError):
            statement = select(CarbonReadingRecord).where(
                CarbonReadingRecord.zone == zone,
                CarbonReadingRecord.lookup_key
                == carbon_lookup_key(data_center_provider, data_center_region),
            )
            latest = await session.scalar(
                statement.order_by(CarbonReadingRecord.observed_at.desc()).limit(1)
            )
            if latest is not None:
                age = utcnow() - latest.observed_at
                if age <= maximum_age:
                    evidence = latest.evidence if age <= freshness_target else "stale"
                    return CarbonReading(
                        zone=latest.zone,
                        intensity_gco2_kwh=latest.intensity_gco2_kwh,
                        observed_at=latest.observed_at,
                        fetched_at=latest.fetched_at,
                        source=latest.source,
                        evidence=evidence,
                        metadata=latest.reading_metadata,
                    )
            now = utcnow()
            return CarbonReading(
                zone=zone,
                intensity_gco2_kwh=275,
                observed_at=now,
                fetched_at=now,
                source="ecoroute-default-no-reading",
                evidence="stale",
                metadata={
                    "available": False,
                    "lookup_mode": "data_center" if data_center_provider else "zone",
                    "data_center_provider": data_center_provider,
                    "data_center_region": data_center_region,
                },
            )
