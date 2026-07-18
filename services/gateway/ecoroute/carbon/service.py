from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecoroute.api.schemas import CarbonReading
from ecoroute.carbon.providers import CarbonAwareSdkProvider, FixtureCarbonProvider
from ecoroute.config import Settings
from ecoroute.db.base import utcnow
from ecoroute.db.models import CarbonReadingRecord


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
    ) -> CarbonReading:
        if self.settings.demo_mode:
            return await FixtureCarbonProvider(demo_scenario or "moderate").reading(zone)
        cached = await self.redis.get(f"ecoroute:carbon:{zone}")
        if cached:
            try:
                return CarbonReading.model_validate_json(cached)
            except ValidationError:
                await self.redis.delete(f"ecoroute:carbon:{zone}")
        try:
            async with asyncio.timeout(0.2):
                reading = await CarbonAwareSdkProvider(self.settings.carbon_aware_base_url).reading(
                    zone
                )
            await self.redis.set(
                f"ecoroute:carbon:{zone}",
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
                )
            )
            return reading
        except (TimeoutError, OSError, ValueError, httpx.HTTPError):
            latest = await session.scalar(
                select(CarbonReadingRecord)
                .where(CarbonReadingRecord.zone == zone)
                .order_by(CarbonReadingRecord.observed_at.desc())
                .limit(1)
            )
            if latest is not None:
                age = utcnow() - latest.observed_at
                if age <= timedelta(minutes=allow_stale_minutes):
                    evidence = latest.evidence if age <= timedelta(minutes=5) else "stale"
                    return CarbonReading(
                        zone=latest.zone,
                        intensity_gco2_kwh=latest.intensity_gco2_kwh,
                        observed_at=latest.observed_at,
                        fetched_at=latest.fetched_at,
                        source=latest.source,
                        evidence=evidence,
                    )
            now = utcnow()
            return CarbonReading(
                zone=zone,
                intensity_gco2_kwh=275,
                observed_at=now,
                fetched_at=now,
                source="ecoroute-default-no-reading",
                evidence="stale",
            )
