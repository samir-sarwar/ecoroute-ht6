from datetime import datetime, timezone

import pytest
from ecoroute.api.schemas import CarbonReading
from ecoroute.carbon.demo_region import (
    choose_demo_region_overlay,
    counterfactual_impact,
    demo_energy_estimate,
    parse_demo_region_candidates,
)


def reading(zone: str, intensity: float) -> CarbonReading:
    now = datetime.now(timezone.utc)
    return CarbonReading(
        zone=zone,
        intensity_gco2_kwh=intensity,
        observed_at=now,
        fetched_at=now,
        source="electricity-maps:v4:lifecycle:flow-traced:zone",
        evidence="measured",
        metadata={"available": True, "lookup_mode": "zone"},
    )


def test_live_overlay_chooses_cleanest_candidate_and_keeps_reference() -> None:
    candidates = parse_demo_region_candidates(
        "east-us-2=US-MIDA-PJM,sweden-central=SE-SE3,poland-central=PL"
    )
    overlay = choose_demo_region_overlay(
        [
            (candidates[0], reading("US-MIDA-PJM", 470)),
            (candidates[1], reading("SE-SE3", 19)),
            (candidates[2], reading("PL", 660)),
        ],
        "east-us-2",
    )

    assert overlay is not None
    assert overlay["reference"]["region"] == "east-us-2"
    assert overlay["target"]["region"] == "sweden-central"
    assert overlay["target"]["intensityGco2Kwh"] == 19
    assert overlay["providerRoutingControlled"] is False
    assert overlay["usesLiveGridData"] is True


def test_counterfactual_is_separate_and_uses_configured_energy() -> None:
    candidates = parse_demo_region_candidates("east-us-2=US-MIDA-PJM,sweden=SE-SE3")
    overlay = choose_demo_region_overlay(
        [
            (candidates[0], reading("US-MIDA-PJM", 500)),
            (candidates[1], reading("SE-SE3", 20)),
        ],
        "east-us-2",
    )
    assert overlay is not None

    impact = counterfactual_impact(
        overlay,
        baseline_energy_kwh=0.01,
        selected_energy_kwh=0.002,
    )

    assert impact["baselineCarbonG"] == pytest.approx(5.0)
    assert impact["targetCarbonG"] == pytest.approx(0.04)
    assert impact["avoidedCarbonG"] == pytest.approx(4.96)
    assert impact["providerRoutingControlled"] is False


def test_overlay_requires_available_reference_region() -> None:
    candidates = parse_demo_region_candidates("sweden=SE-SE3")
    assert (
        choose_demo_region_overlay(
            [(candidates[0], reading("SE-SE3", 20))],
            "east-us-2",
        )
        is None
    )


def test_demo_energy_uses_explicitly_labeled_fallback_for_zero_coefficients() -> None:
    estimate = demo_energy_estimate(
        "frontier",
        input_tokens=1000,
        output_tokens=500,
        configured_energy_kwh=0,
    )

    assert estimate["energyKwh"] == pytest.approx(0.0043)
    assert estimate["source"] == "temporary_demo_tier_assumption_v1"
    assert estimate["simulatedFallback"] is True


def test_demo_energy_prefers_endpoint_coefficients() -> None:
    estimate = demo_energy_estimate(
        "frontier",
        input_tokens=1000,
        output_tokens=500,
        configured_energy_kwh=0.012,
    )

    assert estimate == {
        "energyKwh": 0.012,
        "source": "endpoint_configured_coefficients",
        "simulatedFallback": False,
    }


def test_candidate_configuration_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_demo_region_candidates("east-us-2=US-MIDA-PJM,east-us-2=US-NY-NYIS")
