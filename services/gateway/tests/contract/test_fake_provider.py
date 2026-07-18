import uuid
from decimal import Decimal

import pytest
from ecoroute.api.schemas import ChatCompletionRequest
from ecoroute.config import Settings
from ecoroute.db.models import ModelEndpoint
from ecoroute.providers.fake import FakeProvider


@pytest.mark.asyncio
async def test_fake_provider_openai_shape() -> None:
    endpoint = ModelEndpoint(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="fake",
        provider="fake",
        base_url="http://fake",
        physical_model="fake",
        region="demo",
        grid_zone="demo",
        quality_tier="frontier",
        capabilities=["text"],
        context_window_tokens=1000,
        input_usd_per_million_tokens=Decimal(0),
        output_usd_per_million_tokens=Decimal(0),
        fixed_request_kwh=0,
        input_kwh_per_1k_tokens=0,
        output_kwh_per_1k_tokens=0,
        energy_evidence="simulated",
        latency_p50_ms=0,
        latency_p95_ms=0,
        self_hosted=False,
    )
    response = await FakeProvider(Settings(ECOROUTE_FAKE_PROVIDER_DELAY_MS=0)).chat(
        endpoint,
        ChatCompletionRequest(
            model="support-default", messages=[{"role": "user", "content": "Returns?"}]
        ),
    )
    assert response["object"] == "chat.completion"
    assert response["choices"][0]["message"]["role"] == "assistant"
    assert response["usage"]["total_tokens"] > 0
