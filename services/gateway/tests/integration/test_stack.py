from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import timedelta

import httpx
import pytest
import yaml
from ecoroute.db.base import utcnow
from ecoroute.db.models import (
    CacheEntry,
    CarbonReadingRecord,
    GatewayRequest,
    ImpactRecord,
    Job,
    LogicalModel,
    ModelAttempt,
    RouteDecision,
    Workspace,
)
from ecoroute.db.session import SessionLocal, redis_client
from ecoroute.main import app
from ecoroute_worker.jobs.handlers import handle_carbon_refresh, handle_retention_cleanup
from openai import AsyncOpenAI, NotFoundError
from sqlalchemy import func, select

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))


@pytest.fixture(scope="session", autouse=True)
def migrated_seeded_database() -> None:
    commands = [
        ["alembic", "-c", "services/gateway/alembic.ini", "upgrade", "head"],
        ["alembic", "-c", "services/gateway/alembic.ini", "check"],
        ["alembic", "-c", "services/gateway/alembic.ini", "downgrade", "base"],
        ["alembic", "-c", "services/gateway/alembic.ini", "upgrade", "head"],
        [sys.executable, "-m", "ecoroute.seed"],
        [sys.executable, "-m", "ecoroute.seed"],
    ]
    environment = {
        **os.environ,
        "PYTHONPATH": "services/gateway:services/worker:services/node-agent",
    }
    for command in commands:
        executable = (
            [sys.executable, "-m", "alembic", *command[1:]] if command[0] == "alembic" else command
        )
        subprocess.run(executable, cwd=ROOT, env=environment, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_ephemeral_stack_contracts() -> None:
    await redis_client.flushdb()
    async with SessionLocal() as session:
        assert int(await session.scalar(select(func.count(Workspace.id))) or 0) == 1
        assert int(await session.scalar(select(func.count(LogicalModel.id))) or 0) == 1

    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer ecoroute-integration-key"}
    request_body = {
        "model": "support-default",
        "messages": [
            {"role": "system", "content": "Northstar public support policy v1"},
            {"role": "user", "content": "What is the return window for unused items?"},
        ],
        "temperature": 0,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/v1/chat/completions", headers=headers, json=request_body)
        assert first.status_code == 200
        assert first.headers["x-ecoroute-cache"] == "miss"
        first_request_id = uuid.UUID(first.headers["x-ecoroute-request-id"])

        second = await client.post("/v1/chat/completions", headers=headers, json=request_body)
        assert second.status_code == 200
        assert second.headers["x-ecoroute-cache"] == "exact"

        paraphrase = {
            **request_body,
            "messages": [
                request_body["messages"][0],
                {"role": "user", "content": "How many days can I send back an unused item?"},
            ],
        }
        semantic = await client.post("/v1/chat/completions", headers=headers, json=paraphrase)
        assert semantic.status_code == 200
        assert semantic.headers["x-ecoroute-cache"] == "semantic"

        endpoint_page = await client.get(
            "/api/v1/model-endpoints", headers=headers, params={"limit": 1}
        )
        assert endpoint_page.status_code == 200
        assert len(endpoint_page.json()["items"]) == 1
        assert endpoint_page.json()["nextCursor"]
        endpoint_page_two = await client.get(
            "/api/v1/model-endpoints",
            headers=headers,
            params={"limit": 1, "cursor": endpoint_page.json()["nextCursor"]},
        )
        assert endpoint_page_two.status_code == 200
        assert endpoint_page_two.json()["items"][0]["id"] != endpoint_page.json()["items"][0]["id"]
        request_page = await client.get("/api/v1/requests", headers=headers, params={"limit": 1})
        assert request_page.status_code == 200
        assert request_page.json()["nextCursor"]

        from_time = (utcnow() - timedelta(hours=1)).isoformat()
        to_time = (utcnow() + timedelta(minutes=1)).isoformat()
        summary = await client.get(
            "/api/v1/reports/summary",
            headers=headers,
            params={"from": from_time, "to": to_time},
        )
        assert summary.status_code == 200
        assert summary.json()["requestCount"] >= 3
        assert summary.json()["methodologyVersion"] == "ecoroute-v1"
        csv_export = await client.get(
            "/api/v1/reports/requests.csv",
            headers=headers,
            params={"from": from_time, "to": to_time, "route": "cache"},
        )
        assert csv_export.status_code == 200
        assert csv_export.headers["x-ecoroute-methodology-version"] == "ecoroute-v1"
        assert "generated" not in csv_export.text.splitlines()[0].lower()
        assert any(",cache," in line for line in csv_export.text.splitlines()[1:])
        impact_export = await client.post(
            "/api/v1/reports/impact-framework",
            headers=headers,
            json={"from": from_time, "to": to_time, "route": "cache"},
        )
        assert impact_export.status_code == 200
        manifest = yaml.safe_load(impact_export.text)
        assert manifest["metadata"]["filters"]["route"] == "cache"
        assert "cache" in manifest["tree"]["children"]
        invalid_range = await client.get(
            "/api/v1/reports/summary",
            headers=headers,
            params={"from": to_time, "to": from_time},
        )
        assert invalid_range.status_code == 400

        endpoint_body = {
            "name": "Disposable integration endpoint",
            "provider": "fake",
            "baseUrl": "http://fake-provider:9999/v1",
            "credentialRef": None,
            "physicalModel": "integration-fake-v1",
            "region": "test-local",
            "gridZone": "test-local",
            "qualityTier": "small",
            "capabilities": ["text", "streaming"],
            "contextWindowTokens": 4096,
            "inputUsdPerMillionTokens": 0,
            "outputUsdPerMillionTokens": 0,
            "fixedRequestKwh": 0.001,
            "inputKwhPer1kTokens": 0.001,
            "outputKwhPer1kTokens": 0.001,
            "energyEvidence": "simulated",
            "latencyP50Ms": 10,
            "latencyP95Ms": 20,
            "selfHosted": False,
            "baselineConcurrency": 4,
            "concurrencyTarget": 4,
            "enabled": True,
        }
        created_endpoint = await client.post(
            "/api/v1/model-endpoints", headers=headers, json=endpoint_body
        )
        assert created_endpoint.status_code == 201
        endpoint_id = created_endpoint.json()["id"]
        patched_endpoint = await client.patch(
            f"/api/v1/model-endpoints/{endpoint_id}",
            headers=headers,
            json={"enabled": False, "concurrencyTarget": 2},
        )
        assert patched_endpoint.status_code == 200
        assert patched_endpoint.json()["enabled"] is False
        assert patched_endpoint.json()["concurrencyTarget"] == 2
        deleted_endpoint = await client.delete(
            f"/api/v1/model-endpoints/{endpoint_id}", headers=headers
        )
        assert deleted_endpoint.status_code == 204
        assert (
            await client.get(f"/api/v1/model-endpoints/{endpoint_id}", headers=headers)
        ).status_code == 404

        load_headers = {**headers, "Idempotency-Key": "integration-demo-load"}
        job_one = await client.post(
            "/api/v1/demo/load",
            headers=load_headers,
            json={"requestCount": 1, "concurrency": 1},
        )
        job_two = await client.post(
            "/api/v1/demo/load",
            headers=load_headers,
            json={"requestCount": 1, "concurrency": 1},
        )
        assert job_one.status_code == job_two.status_code == 202
        assert job_one.json()["jobId"] == job_two.json()["jobId"]

    sdk_http = httpx.AsyncClient(transport=transport, base_url="http://test")
    sdk = AsyncOpenAI(
        api_key="ecoroute-integration-key",
        base_url="http://test/v1",
        http_client=sdk_http,
    )
    try:
        raw_sdk_response = await sdk.chat.completions.with_raw_response.create(
            model="support-default",
            messages=[
                {"role": "system", "content": "Northstar public support policy sdk-v1"},
                {"role": "developer", "content": "Be concise."},
                {"role": "user", "content": "How long does standard shipping take?"},
            ],
            temperature=0,
        )
        parsed_sdk_response = raw_sdk_response.parse()
        assert parsed_sdk_response.choices[0].message.content
        assert raw_sdk_response.headers["x-ecoroute-cache"] in {
            "miss",
            "exact",
            "semantic",
        }
        assert raw_sdk_response.headers["x-ecoroute-endpoint-id"]

        sdk_stream = await sdk.chat.completions.create(
            model="support-default",
            messages=[
                {"role": "system", "content": "Northstar public support policy sdk-stream"},
                {"role": "user", "content": "What is the refund timing?"},
            ],
            temperature=0,
            stream=True,
            stream_options={"include_usage": True},
        )
        sdk_chunks = [chunk async for chunk in sdk_stream]
        assert any(chunk.choices for chunk in sdk_chunks)
        assert sdk_chunks[-1].usage is not None

        tool_stream = await sdk.chat.completions.create(
            model="support-default",
            messages=[{"role": "user", "content": "Stream a tool lookup."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_order",
                        "description": "Lookup an order",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            stream=True,
        )
        tool_chunks = [chunk async for chunk in tool_stream]
        assert any(choice.delta.tool_calls for chunk in tool_chunks for choice in chunk.choices)
        assert any(
            choice.finish_reason == "tool_calls"
            for chunk in tool_chunks
            for choice in chunk.choices
        )

        structured = await sdk.chat.completions.create(
            model="support-default",
            messages=[
                {"role": "system", "content": "Northstar sdk structured-v1"},
                {"role": "user", "content": "Return the return policy as JSON."},
            ],
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "support_answer",
                    "strict": True,
                    "schema": {"type": "object", "additionalProperties": True},
                },
            },
        )
        assert structured.choices[0].message.content

        tool_response = await sdk.chat.completions.with_raw_response.create(
            model="support-default",
            messages=[{"role": "user", "content": "Look up order status."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_order",
                        "description": "Lookup an order",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
        assert tool_response.parse().choices[0].message.tool_calls
        assert tool_response.headers["x-ecoroute-route"] == "frontier"

        with pytest.raises(NotFoundError) as missing:
            await sdk.chat.completions.create(
                model="unknown-logical-model",
                messages=[{"role": "user", "content": "Hello"}],
            )
        assert missing.value.code == "model_not_found"
    finally:
        await sdk.close()

    async with SessionLocal() as session:
        assert await session.scalar(
            select(RouteDecision).where(RouteDecision.request_id == first_request_id)
        )
        assert await session.scalar(
            select(ModelAttempt).where(ModelAttempt.request_id == first_request_id)
        )
        assert await session.scalar(
            select(ImpactRecord).where(
                ImpactRecord.request_id == first_request_id,
                ImpactRecord.strategy == "end_to_end",
            )
        )
        cache_entry = await session.scalar(
            select(CacheEntry).where(CacheEntry.source_request_id == first_request_id)
        )
        assert cache_entry is not None and cache_entry.embedding is not None
        nearest = await session.scalar(
            select(CacheEntry)
            .where(
                CacheEntry.workspace_id == cache_entry.workspace_id,
                CacheEntry.logical_model_id == cache_entry.logical_model_id,
                CacheEntry.invalidated_at.is_(None),
            )
            .order_by(CacheEntry.embedding.cosine_distance(cache_entry.embedding))
            .limit(1)
        )
        assert nearest is not None and nearest.id == cache_entry.id
        request = await session.get(GatewayRequest, first_request_id)
        assert request is not None
        assert "normalized_text" not in request.request_features
        request.started_at = utcnow() - timedelta(days=31)
        request.redacted_prompt_preview = "old redacted preview"
        await session.commit()
        workspace = await session.scalar(select(Workspace).limit(1))
        assert workspace is not None
        maintenance_job = Job(
            workspace_id=workspace.id,
            kind="retention.cleanup",
            status="running",
            idempotency_key="integration-retention",
            input={},
        )
        carbon_job = Job(
            workspace_id=workspace.id,
            kind="carbon.refresh",
            status="running",
            idempotency_key="integration-carbon",
            input={"zones": ["demo-local"]},
        )
        session.add_all([maintenance_job, carbon_job])
        await session.commit()

    carbon_result = await handle_carbon_refresh(carbon_job)
    assert carbon_result["readings"][0]["evidence"] == "simulated"
    retention_result = await handle_retention_cleanup(maintenance_job)
    assert retention_result["requests_minimized"] >= 1
    async with SessionLocal() as session:
        assert await session.scalar(
            select(CarbonReadingRecord).where(CarbonReadingRecord.zone == "demo-local")
        )
        request = await session.get(GatewayRequest, first_request_id)
        assert request is not None and request.redacted_prompt_preview is None
        assert (
            int(
                await session.scalar(
                    select(func.count(Job.id)).where(Job.idempotency_key == "integration-demo-load")
                )
                or 0
            )
            == 1
        )

    await redis_client.set("integration-expiry", "value", ex=1)
    await asyncio.sleep(1.1)
    assert await redis_client.get("integration-expiry") is None

    stream = "integration:recovery"
    group = "integration-workers"
    await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    await redis_client.xadd(stream, {"job_id": "durable"})
    received = await redis_client.xreadgroup(group, "worker-a", {stream: ">"}, count=1)
    assert received
    claimed = await redis_client.xautoclaim(
        stream, group, "worker-b", min_idle_time=0, start_id="0-0", count=1
    )
    assert claimed[1]
