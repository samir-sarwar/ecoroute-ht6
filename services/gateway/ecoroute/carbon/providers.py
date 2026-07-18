from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ecoroute.api.schemas import CarbonReading

FIXTURES = {"clean": 100.0, "moderate": 275.0, "dirty": 650.0}


class FixtureCarbonProvider:
    def __init__(self, scenario: str = "moderate") -> None:
        self.scenario = scenario

    async def reading(self, zone: str) -> CarbonReading:
        now = datetime.now(timezone.utc)
        intensity = 80.0 if zone == "demo-remote" else FIXTURES.get(self.scenario, 275.0)
        return CarbonReading(
            zone=zone,
            intensity_gco2_kwh=intensity,
            observed_at=now,
            fetched_at=now,
            source=f"ecoroute-fixture:{self.scenario}",
            evidence="simulated",
        )


class CarbonAwareSdkProvider:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def reading(self, zone: str) -> CarbonReading:
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
            observed_at=datetime.fromisoformat(observed.replace("Z", "+00:00"))
            if observed
            else now,
            fetched_at=now,
            source=str(item.get("source", "carbon-aware-sdk")),
            evidence="estimated",
        )
