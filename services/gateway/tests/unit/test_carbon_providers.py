from __future__ import annotations

import httpx
import pytest
from ecoroute.carbon.providers import ElectricityMapsProvider
from ecoroute.carbon.service import carbon_cache_key, carbon_lookup_key


def test_carbon_lookup_identity_separates_data_center_mappings() -> None:
    assert carbon_lookup_key() == "zone"
    assert carbon_lookup_key("GCP", "EUROPE_WEST1") == "dc:gcp:europe-west1"
    assert carbon_cache_key("DE", "GCP", "EUROPE_WEST1") == (
        "ecoroute:carbon:DE:dc:gcp:europe-west1"
    )


async def test_electricity_maps_v4_zone_reading_preserves_provenance() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v4/carbon-intensity/latest"
        assert request.headers["auth-token"] == "test-token"
        assert request.url.params["zone"] == "CA-ON"
        assert request.url.params["disableCallerLookup"] == "true"
        assert request.url.params["emissionFactorType"] == "lifecycle"
        assert request.url.params["flowTraced"] == "true"
        assert request.url.params["temporalGranularity"] == "5_minutes"
        return httpx.Response(
            200,
            json={
                "zone": "CA-ON",
                "carbonIntensity": 42.5,
                "datetime": "2026-07-18T12:00:00Z",
                "updatedAt": "2026-07-18T12:03:00Z",
                "isEstimated": False,
                "emissionFactorType": "lifecycle",
                "temporalGranularity": "hourly",
            },
        )

    provider = ElectricityMapsProvider(
        "test-token",
        "https://api.electricitymaps.com/v4",
        transport=httpx.MockTransport(handler),
    )
    reading = await provider.reading("CA-ON")

    assert reading.intensity_gco2_kwh == 42.5
    assert reading.evidence == "measured"
    assert reading.source == "electricity-maps:v4:lifecycle:flow-traced:zone"
    assert reading.metadata == {
        "provider": "electricity_maps",
        "api_version": "v4",
        "lookup_mode": "zone",
        "data_center_provider": None,
        "data_center_region": None,
        "is_estimated": False,
        "estimation_method": None,
        "emission_factor_type": "lifecycle",
        "flow_traced": True,
        "temporal_granularity": "hourly",
        "updated_at": "2026-07-18T12:03:00Z",
    }


async def test_electricity_maps_data_center_lookup_is_fail_closed() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "zone": "DE",
                "carbonIntensity": 210,
                "datetime": "2026-07-18T12:00:00Z",
                "isEstimated": True,
                "estimationMethod": "temporal-gap",
            },
        )

    provider = ElectricityMapsProvider(
        "test-token",
        transport=httpx.MockTransport(handler),
    )
    reading = await provider.reading(
        "DE",
        data_center_provider="gcp",
        data_center_region="europe-west1",
    )
    assert reading.evidence == "estimated"
    assert reading.metadata["lookup_mode"] == "data_center"
    assert requests[0].url.params["dataCenterProvider"] == "gcp"
    assert requests[0].url.params["dataCenterRegion"] == "europe-west1"
    assert "zone" not in requests[0].url.params

    with pytest.raises(ValueError, match="resolved zone"):
        await provider.reading(
            "FR",
            data_center_provider="gcp",
            data_center_region="europe-west1",
        )


async def test_electricity_maps_rejects_incomplete_response() -> None:
    provider = ElectricityMapsProvider(
        "test-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"zone": "CA-ON"})),
    )
    with pytest.raises(ValueError, match="omitted carbon intensity"):
        await provider.reading("CA-ON")
