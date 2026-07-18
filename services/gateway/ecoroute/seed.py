from __future__ import annotations

import asyncio
import hashlib
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from ecoroute.api.schemas import PRESET_CONFIG, RoutingPolicyConfig
from ecoroute.db.models import (
    LogicalModel,
    LogicalModelEndpoint,
    ModelEndpoint,
    PolicyDocument,
    RoutingPolicy,
    SlmProfile,
    Workspace,
)
from ecoroute.db.session import SessionLocal

NAMESPACE = uuid.UUID("1d859d5e-c2d7-4dd4-819f-c2cfe098d90c")


def fixture_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


POLICIES = {
    "returns-30-day": "Unused items may be returned within 30 days.",
    "final-sale": "Final-sale items cannot be returned except when defective.",
    "exchange-stock": "Exchanges depend on current inventory.",
    "shipping-standard": "Standard shipping estimate is 3-5 business days.",
    "shipping-delay": "Escalate after 7 business days without carrier movement.",
    "refund-timing": "Approved refunds may take 5-10 business days to appear.",
}


async def seed() -> None:
    async with SessionLocal() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.slug == "demo"))
        if workspace is None:
            workspace = Workspace(id=fixture_id("workspace"), slug="demo", name="EcoRoute Demo")
            session.add(workspace)
            await session.flush()

        profile = await session.get(SlmProfile, fixture_id("northstar-profile"))
        if profile is None:
            profile = SlmProfile(
                id=fixture_id("northstar-profile"),
                workspace_id=workspace.id,
                name="Northstar Support",
                description="Fictional e-commerce support profile for the credential-free demo.",
                business_name="Northstar Outfitters",
                definition={
                    "allowed_tasks": [
                        "policy_qa",
                        "summarization",
                        "classification",
                        "extraction",
                        "reply_draft",
                    ],
                    "forbidden_topics": [
                        "legal advice",
                        "payments",
                        "account mutations",
                        "medical advice",
                    ],
                    "supported_languages": ["en"],
                    "tone": "clear, calm, and concise",
                    "output_contract": "support_answer_v1",
                    "training_example_target": 1500,
                    "fictional_demo_facts": True,
                },
                status="ready",
            )
            session.add(profile)
            await session.flush()
            for key, content in POLICIES.items():
                session.add(
                    PolicyDocument(
                        id=fixture_id(f"policy:{key}"),
                        slm_profile_id=profile.id,
                        policy_key=key,
                        title=key.replace("-", " ").title(),
                        content=content,
                        version=1,
                        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                    )
                )

        endpoint_specs: list[dict[str, Any]] = [
            {
                "key": "support-slm",
                "name": "demo-support-slm",
                "quality_tier": "specialized",
                "region": "demo-local",
                "grid_zone": "demo-local",
                "input_price": "0.10",
                "output_price": "0.30",
                "fixed": 0.00004,
                "input_energy": 0.00010,
                "output_energy": 0.00030,
                "p50": 90,
                "p95": 180,
                "slm_profile_id": profile.id,
            },
            {
                "key": "small",
                "name": "demo-small",
                "quality_tier": "small",
                "region": "demo-local",
                "grid_zone": "demo-local",
                "input_price": "0.20",
                "output_price": "0.60",
                "fixed": 0.00008,
                "input_energy": 0.00020,
                "output_energy": 0.00060,
                "p50": 120,
                "p95": 260,
                "slm_profile_id": None,
            },
            {
                "key": "frontier-local",
                "name": "demo-frontier-local",
                "quality_tier": "frontier",
                "region": "demo-local",
                "grid_zone": "demo-local",
                "input_price": "2.00",
                "output_price": "8.00",
                "fixed": 0.0008,
                "input_energy": 0.0015,
                "output_energy": 0.0040,
                "p50": 420,
                "p95": 800,
                "slm_profile_id": None,
            },
            {
                "key": "frontier-remote",
                "name": "demo-frontier-remote",
                "quality_tier": "frontier",
                "region": "demo-remote",
                "grid_zone": "demo-remote",
                "input_price": "2.00",
                "output_price": "8.00",
                "fixed": 0.0008,
                "input_energy": 0.0015,
                "output_energy": 0.0040,
                "p50": 520,
                "p95": 1050,
                "slm_profile_id": None,
            },
        ]
        endpoints: list[ModelEndpoint] = []
        for spec in endpoint_specs:
            endpoint = await session.get(ModelEndpoint, fixture_id(f"endpoint:{spec['key']}"))
            if endpoint is None:
                endpoint = ModelEndpoint(
                    id=fixture_id(f"endpoint:{spec['key']}"),
                    workspace_id=workspace.id,
                    name=spec["name"],
                    provider="fake",
                    base_url="http://gateway:8000/_internal/fake/v1",
                    credential_ref=None,
                    physical_model=spec["name"],
                    region=spec["region"],
                    grid_zone=spec["grid_zone"],
                    grid_lookup_mode="zone",
                    processing_location_evidence="simulated",
                    grid_attribution="simulated",
                    quality_tier=spec["quality_tier"],
                    capabilities=["text", "json_schema", "streaming"]
                    + (["tools"] if spec["quality_tier"] == "frontier" else []),
                    context_window_tokens=32768,
                    input_usd_per_million_tokens=Decimal(str(spec["input_price"])),
                    output_usd_per_million_tokens=Decimal(str(spec["output_price"])),
                    fixed_request_kwh=spec["fixed"],
                    input_kwh_per_1k_tokens=spec["input_energy"],
                    output_kwh_per_1k_tokens=spec["output_energy"],
                    energy_evidence="simulated",
                    latency_p50_ms=spec["p50"],
                    latency_p95_ms=spec["p95"],
                    self_hosted=spec["quality_tier"] in {"small", "specialized"},
                    slm_profile_id=spec["slm_profile_id"],
                    health_state="healthy",
                    coefficient_version="demo-v1",
                )
                session.add(endpoint)
            endpoints.append(endpoint)
        await session.flush()

        policy = await session.get(RoutingPolicy, fixture_id("policy:eco-v1"))
        if policy is None:
            weights, max_cost = PRESET_CONFIG["eco"]
            config = RoutingPolicyConfig(
                name="Eco demo policy",
                preset="eco",
                enabled_endpoint_ids=[item.id for item in endpoints],
                max_cost_increase_pct=max_cost,
                weights=weights,
            )
            policy = RoutingPolicy(
                id=fixture_id("policy:eco-v1"),
                workspace_id=workspace.id,
                family_id=fixture_id("policy-family:eco"),
                version_number=1,
                name=config.name,
                preset=config.preset,
                config=config.model_dump(mode="json"),
            )
            session.add(policy)
            await session.flush()

        logical = await session.get(LogicalModel, fixture_id("logical:support-default"))
        baseline = next(item for item in endpoints if item.name == "demo-frontier-local")
        if logical is None:
            logical = LogicalModel(
                id=fixture_id("logical:support-default"),
                workspace_id=workspace.id,
                alias="support-default",
                display_name="Northstar Support",
                baseline_endpoint_id=baseline.id,
                required_fallback_endpoint_id=baseline.id,
                active_policy_id=policy.id,
            )
            session.add(logical)
            await session.flush()
        for priority, endpoint in enumerate(endpoints, 1):
            join_key = (logical.id, endpoint.id)
            if await session.get(LogicalModelEndpoint, join_key) is None:
                session.add(
                    LogicalModelEndpoint(
                        logical_model_id=logical.id,
                        endpoint_id=endpoint.id,
                        priority=priority * 10,
                    )
                )
        await session.commit()
        print("Seeded EcoRoute demo workspace, policy, endpoints, and Northstar profile.")


if __name__ == "__main__":
    asyncio.run(seed())
