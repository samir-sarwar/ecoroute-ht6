import uuid
from decimal import Decimal

import pytest
from ecoroute.api.schemas import RouterClassification
from ecoroute.carbon.impact import calculate_impact, millijoules_to_kwh, sampled_watts_to_kwh
from ecoroute.routing.engine import EndpointCandidate
from ecoroute.routing.quality import verify_output

CLASSIFICATION = RouterClassification(
    complexity="low",
    task_type="policy_qa",
    risk="low",
    slm_eligible=True,
    cache_eligible=True,
    required_capabilities=["text"],
    predicted_output_tokens=80,
    confidence=0.98,
    rationale_code="PUBLIC_POLICY_LOOKUP",
)


def test_energy_unit_conversions() -> None:
    assert millijoules_to_kwh(3_600_000_000) == 1
    assert sampled_watts_to_kwh([(0, 100), (3600, 100)]) == 0.1


def test_support_contract_passes_and_strips_answer() -> None:
    verdict = verify_output(
        '{"answer":"Unused items can be returned within 30 days.","confidence":0.95,"policy_ids":["returns-30-day"],"needs_human":false}',
        CLASSIFICATION,
        specialized=True,
    )
    assert verdict.passed
    assert verdict.answer == "Unused items can be returned within 30 days."


def test_unknown_policy_and_prohibited_promise_fail() -> None:
    unknown = verify_output(
        '{"answer":"Answer","confidence":0.95,"policy_ids":["invented"],"needs_human":false}',
        CLASSIFICATION,
        specialized=True,
    )
    promise = verify_output("I have issued the refund.", CLASSIFICATION, specialized=False)
    assert not unknown.passed and not promise.passed


def test_json_schema_is_enforced_not_just_json_parsed() -> None:
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "schema": {
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    }
    good = verify_output(
        '{"answer":"Thirty days"}',
        CLASSIFICATION,
        specialized=False,
        response_format=response_format,
    )
    bad = verify_output(
        '{"policy_ids":[]}',
        CLASSIFICATION,
        specialized=False,
        response_format=response_format,
    )
    assert good.passed
    assert not bad.passed
    assert bad.reason == "json_schema_validation_failed"


def _impact_endpoint(name: str, fixed_kwh: float, intensity: float, cost: str) -> EndpointCandidate:
    return EndpointCandidate(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        name=name,
        provider="fake",
        quality_tier="frontier",
        capabilities={"text"},
        context_window_tokens=4096,
        input_usd_per_million_tokens=Decimal(cost),
        output_usd_per_million_tokens=Decimal(cost),
        fixed_request_kwh=fixed_kwh,
        input_kwh_per_1k_tokens=0.001,
        output_kwh_per_1k_tokens=0.002,
        energy_evidence="estimated",
        latency_p95_ms=100,
        grid_intensity=intensity,
        enabled=True,
        health_state="healthy",
        slm_profile_id=None,
    )


def test_impact_uses_same_token_counts_and_keeps_raw_increase() -> None:
    baseline = _impact_endpoint("baseline", 0.01, 500, "10")
    selected = _impact_endpoint("selected", 0.02, 500, "5")
    impact = calculate_impact(
        baseline,
        selected,
        1000,
        500,
        router_energy_kwh=0.001,
    )
    assert impact.baseline_energy_kwh == 0.012
    assert impact.actual_energy_kwh == pytest.approx(0.023)
    assert impact.baseline_carbon_g == 6
    assert impact.actual_carbon_g == pytest.approx(11.5)
    assert impact.raw_carbon_delta_g == pytest.approx(-5.5)
    assert impact.avoided_carbon_g == 0
    assert impact.baseline_cost_usd == Decimal("0.015")
    assert impact.actual_cost_usd == Decimal("0.0075")
