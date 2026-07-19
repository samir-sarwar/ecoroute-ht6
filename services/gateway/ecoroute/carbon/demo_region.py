from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ecoroute.api.schemas import CarbonReading

DEMO_TIER_ENERGY_COEFFICIENTS: dict[str, tuple[float, float, float]] = {
    "specialized": (0.00004, 0.00010, 0.00030),
    "small": (0.00008, 0.00020, 0.00060),
    "standard": (0.00030, 0.00070, 0.00180),
    "frontier": (0.00080, 0.00150, 0.00400),
}


@dataclass(frozen=True)
class DemoRegionCandidate:
    region: str
    zone: str


def parse_demo_region_candidates(value: str) -> list[DemoRegionCandidate]:
    candidates: list[DemoRegionCandidate] = []
    seen_regions: set[str] = set()
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        region, separator, zone = item.partition("=")
        region = region.strip().casefold().replace("_", "-")
        zone = zone.strip()
        if not separator or not region or not zone:
            raise ValueError("demo region candidates must use region=grid-zone entries")
        if region in seen_regions:
            raise ValueError(f"duplicate demo region candidate: {region}")
        seen_regions.add(region)
        candidates.append(DemoRegionCandidate(region=region, zone=zone))
    if not candidates:
        raise ValueError("at least one demo region candidate is required")
    return candidates


def choose_demo_region_overlay(
    readings: list[tuple[DemoRegionCandidate, CarbonReading]],
    reference_region: str,
) -> dict[str, Any] | None:
    available = [
        (candidate, reading)
        for candidate, reading in readings
        if reading.source != "ecoroute-default-no-reading"
        and reading.metadata.get("available") is not False
    ]
    if not available:
        return None
    normalized_reference = reference_region.casefold().replace("_", "-")
    reference = next(
        (item for item in available if item[0].region == normalized_reference),
        None,
    )
    if reference is None:
        return None
    target = min(
        available,
        key=lambda item: (item[1].intensity_gco2_kwh, item[0].region),
    )
    return {
        "mode": "demo_counterfactual",
        "providerRoutingControlled": False,
        "usesLiveGridData": all(
            reading.evidence != "simulated" for _, reading in available
        ),
        "reference": _region_json(*reference),
        "target": _region_json(*target),
        "candidates": [_region_json(candidate, reading) for candidate, reading in available],
        "disclaimer": (
            "Demo target only: Azure Global Standard chooses the actual processing region; "
            "EcoRoute cannot force or verify this target region."
        ),
    }


def counterfactual_impact(
    overlay: dict[str, Any],
    *,
    baseline_energy_kwh: float,
    selected_energy_kwh: float,
) -> dict[str, Any]:
    reference = overlay["reference"]
    target = overlay["target"]
    baseline_carbon = baseline_energy_kwh * float(reference["intensityGco2Kwh"])
    target_carbon = selected_energy_kwh * float(target["intensityGco2Kwh"])
    raw_delta = baseline_carbon - target_carbon
    return {
        "mode": "demo_counterfactual",
        "baselineCarbonG": baseline_carbon,
        "targetCarbonG": target_carbon,
        "rawCarbonDeltaG": raw_delta,
        "avoidedCarbonG": max(0.0, raw_delta),
        "reference": reference,
        "target": target,
        "calculation": "configured_energy_kwh_times_live_grid_intensity",
        "providerRoutingControlled": False,
        "disclaimer": overlay["disclaimer"],
    }


def demo_energy_estimate(
    quality_tier: str,
    *,
    input_tokens: int,
    output_tokens: int,
    configured_energy_kwh: float,
) -> dict[str, Any]:
    if configured_energy_kwh > 0:
        return {
            "energyKwh": configured_energy_kwh,
            "source": "endpoint_configured_coefficients",
            "simulatedFallback": False,
        }
    fixed, input_per_1k, output_per_1k = DEMO_TIER_ENERGY_COEFFICIENTS.get(
        quality_tier,
        DEMO_TIER_ENERGY_COEFFICIENTS["standard"],
    )
    return {
        "energyKwh": (
            fixed + input_tokens * input_per_1k / 1000 + output_tokens * output_per_1k / 1000
        ),
        "source": "temporary_demo_tier_assumption_v1",
        "simulatedFallback": True,
        "coefficients": {
            "fixedRequestKwh": fixed,
            "inputKwhPer1kTokens": input_per_1k,
            "outputKwhPer1kTokens": output_per_1k,
        },
    }


def _region_json(candidate: DemoRegionCandidate, reading: CarbonReading) -> dict[str, Any]:
    return {
        "region": candidate.region,
        "zone": reading.zone,
        "intensityGco2Kwh": reading.intensity_gco2_kwh,
        "observedAt": reading.observed_at.isoformat(),
        "source": reading.source,
        "evidence": reading.evidence,
    }
