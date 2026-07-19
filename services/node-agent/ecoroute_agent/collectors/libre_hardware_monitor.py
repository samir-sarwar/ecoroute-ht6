from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Any

import httpx


_NUMBER = re.compile(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)")


def cpu_package_power_watts(payload: Any) -> float | None:
    """Return the CPU Package power sensor from LHM's nested data.json response."""
    candidates: list[tuple[int, float]] = []

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        text = str(node.get("Text", "")).strip().casefold()
        sensor_id = str(node.get("SensorId", node.get("Identifier", ""))).casefold()
        raw_value = node.get("Value")
        value_text = str(raw_value).strip() if raw_value is not None else ""
        is_power = "/power/" in sensor_id or value_text.casefold().endswith(" w")
        if text == "cpu package" and is_power:
            match = _NUMBER.search(value_text)
            if match:
                try:
                    value = float(match.group(0).replace(",", "."))
                except ValueError:
                    value = math.nan
                if math.isfinite(value) and value >= 0:
                    candidates.append((2 if "/power/" in sensor_id else 1, value))
        for child in node.get("Children", []):
            visit(child)

    visit(payload)
    return max(candidates, default=(0, math.nan))[1] if candidates else None


class LibreHardwareMonitorPowerSampler:
    """Integrate Windows CPU-package watts exposed by LHM into phase energy."""

    def __init__(self, url: str, *, interval_seconds: float = 0.5) -> None:
        self.url = url
        self.interval_seconds = max(0.2, interval_seconds)
        self.samples: list[tuple[float, float]] = []
        self.last_error: str | None = None

    async def _sample(self, client: httpx.AsyncClient) -> None:
        try:
            response = await client.get(self.url)
            response.raise_for_status()
            watts = cpu_package_power_watts(response.json())
            if watts is None:
                raise ValueError("CPU Package power sensor was not present")
            self.samples.append((time.monotonic(), watts))
            self.last_error = None
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"

    async def run(self, stop: asyncio.Event) -> None:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                while not stop.is_set():
                    await self._sample(client)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
                    except TimeoutError:
                        pass
                await self._sample(client)
        except Exception as exc:  # Optional host telemetry must never fail a benchmark.
            self.last_error = f"{type(exc).__name__}: {exc}"

    @property
    def energy_kwh(self) -> float | None:
        if len(self.samples) < 2:
            return None
        joules = 0.0
        for (before_time, before_watts), (after_time, after_watts) in zip(
            self.samples, self.samples[1:], strict=False
        ):
            joules += (before_watts + after_watts) * 0.5 * max(0.0, after_time - before_time)
        return joules / 3_600_000

    @property
    def average_power_watts(self) -> float | None:
        if len(self.samples) < 2:
            return None
        duration = self.samples[-1][0] - self.samples[0][0]
        energy = self.energy_kwh
        return energy * 3_600_000 / duration if energy is not None and duration > 0 else None
