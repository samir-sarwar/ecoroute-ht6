from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ecoroute.routing.engine import EndpointCandidate, estimate_cost, estimate_energy


@dataclass(frozen=True)
class ImpactCalculation:
    baseline_energy_kwh: float
    actual_energy_kwh: float
    baseline_carbon_g: float
    actual_carbon_g: float
    raw_carbon_delta_g: float
    avoided_carbon_g: float
    baseline_cost_usd: Decimal
    actual_cost_usd: Decimal
    cost_delta_usd: Decimal


def calculate_impact(
    baseline: EndpointCandidate,
    selected: EndpointCandidate,
    input_tokens: int,
    output_tokens: int,
    *,
    router_energy_kwh: float = 0,
) -> ImpactCalculation:
    baseline_energy = estimate_energy(baseline, input_tokens, output_tokens)
    actual_energy = estimate_energy(selected, input_tokens, output_tokens) + router_energy_kwh
    baseline_carbon = baseline_energy * baseline.grid_intensity
    actual_carbon = actual_energy * selected.grid_intensity
    raw_delta = baseline_carbon - actual_carbon
    baseline_cost = estimate_cost(baseline, input_tokens, output_tokens)
    actual_cost = estimate_cost(selected, input_tokens, output_tokens)
    return ImpactCalculation(
        baseline_energy_kwh=baseline_energy,
        actual_energy_kwh=actual_energy,
        baseline_carbon_g=baseline_carbon,
        actual_carbon_g=actual_carbon,
        raw_carbon_delta_g=raw_delta,
        avoided_carbon_g=max(0, raw_delta),
        baseline_cost_usd=baseline_cost,
        actual_cost_usd=actual_cost,
        cost_delta_usd=actual_cost - baseline_cost,
    )


def millijoules_to_kwh(value: float) -> float:
    return value / 3_600_000_000


def sampled_watts_to_kwh(samples: list[tuple[float, float]]) -> float:
    """Integrate sorted (timestamp_seconds, watts) samples with trapezoids."""
    if len(samples) < 2:
        return 0.0
    watt_seconds = sum(
        ((left[1] + right[1]) / 2) * (right[0] - left[0])
        for left, right in zip(samples, samples[1:], strict=False)
    )
    return watt_seconds / 3_600_000
