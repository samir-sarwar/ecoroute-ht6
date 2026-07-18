from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from ecoroute.api.events import publish_event
from ecoroute.carbon.providers import FixtureCarbonProvider, configured_carbon_provider
from ecoroute.carbon.service import carbon_cache_key, carbon_lookup_key
from ecoroute.config import get_settings
from ecoroute.db.base import utcnow
from ecoroute.db.models import (
    Benchmark,
    CacheEntry,
    CarbonReadingRecord,
    Dataset,
    DatasetExample,
    GatewayRequest,
    Job,
    ModelEndpoint,
    NodeAgent,
    PolicyDocument,
    SlmProfile,
    TelemetrySample,
    TrainingRun,
    TrainingRunEvent,
)
from ecoroute.db.session import SessionLocal, redis_client
from ecoroute.providers.registry import ProviderRegistry
from sqlalchemy import delete, func, select

from ecoroute_worker.freesolo.cli import FreeSoloCli, build_command
from ecoroute_worker.gemini.generator import GeminiDatasetGenerator, process_examples

settings = get_settings()
providers = ProviderRegistry(settings)
ROOT = Path(__file__).resolve().parents[4]


class PermanentJobError(RuntimeError):
    """A validation or configuration failure that must not be retried."""


@dataclass(frozen=True)
class DeferredJob:
    delay_seconds: int
    output: dict[str, Any]


Handler = Callable[[Job], Awaitable[dict[str, Any] | DeferredJob]]


def _dataset_manifest(examples: list[DatasetExample]) -> str:
    rows = [
        {
            "external_id": item.external_id,
            "split": item.split,
            "input": item.input,
            "output": item.output,
            "metadata": item.example_metadata,
        }
        for item in sorted(examples, key=lambda value: value.external_id)
    ]
    return hashlib.sha256(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows).encode()
    ).hexdigest()


async def _training_event(run: TrainingRun, event_type: str, payload: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        sequence = (
            int(
                await session.scalar(
                    select(func.max(TrainingRunEvent.sequence)).where(
                        TrainingRunEvent.training_run_id == run.id
                    )
                )
                or 0
            )
            + 1
        )
        session.add(
            TrainingRunEvent(
                training_run_id=run.id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
            )
        )
        await session.commit()


async def _publish_job_event(job: Job, event_type: str, data: dict[str, Any]) -> None:
    await publish_event(
        redis_client,
        settings,
        job.workspace_id,
        event_type,
        {"jobId": str(job.id), "kind": job.kind, **data},
    )


async def handle_dataset_generate(job: Job) -> dict[str, Any]:
    if not settings.gemini_api_key:
        raise PermanentJobError(
            "gemini_not_configured: set GEMINI_API_KEY and retry with a new dataset version"
        )
    dataset_id = uuid.UUID(job.input["dataset_id"])
    profile_id = uuid.UUID(job.input["profile_id"])
    async with SessionLocal() as session:
        dataset = await session.get(Dataset, dataset_id)
        profile = await session.get(SlmProfile, profile_id)
        if dataset is None or profile is None:
            raise PermanentJobError("dataset or SLM profile no longer exists")
        if dataset.status != "generating":
            raise PermanentJobError("dataset is not in generating state")
        policy_rows = list(
            (
                await session.scalars(
                    select(PolicyDocument).where(
                        PolicyDocument.slm_profile_id == profile_id,
                        PolicyDocument.active.is_(True),
                    )
                )
            ).all()
        )
        policies = {item.policy_key: item.content for item in policy_rows}
        if not policies:
            raise PermanentJobError("SLM profile has no active policy documents")
        target = min(int(dataset.generation_config.get("target", 100)), 2_000)
        distribution = dataset.generation_config.get("distribution", {})
        generator = GeminiDatasetGenerator(settings.gemini_api_key, settings.gemini_dataset_model)
        generated = []
        for start in range(0, target, 50):
            count = min(50, target - start)
            generated.extend(
                await generator.generate_batch(
                    batch_id=f"{dataset.id}:{start // 50 + 1}",
                    business_profile=profile.definition,
                    policies=policies,
                    count=count,
                    distribution=distribution,
                )
            )
            await _publish_job_event(
                job,
                "training.status",
                {"datasetId": str(dataset.id), "generated": min(target, start + count)},
            )
        processed, manifest = process_examples(generated, set(policies), distribution)
        if not processed:
            raise PermanentJobError("all generated examples failed validation")
        for item in processed:
            session.add(
                DatasetExample(
                    dataset_id=dataset.id,
                    external_id=item.external_id,
                    split=item.split,
                    input=item.input,
                    output=item.output,
                    example_metadata=item.metadata,
                    embedding=item.embedding,
                    approved=False,
                )
            )
        dataset.example_count = len(processed)
        dataset.manifest_sha256 = manifest
        dataset.status = "review_required"
        await session.commit()
        return {
            "dataset_id": str(dataset.id),
            "example_count": len(processed),
            "manifest": manifest,
        }


async def handle_dataset_finalize(job: Job) -> dict[str, Any]:
    dataset_id = uuid.UUID(job.input["dataset_id"])
    async with SessionLocal() as session:
        dataset = await session.get(Dataset, dataset_id)
        if dataset is None:
            raise PermanentJobError("dataset no longer exists")
        examples = list(
            (
                await session.scalars(
                    select(DatasetExample).where(DatasetExample.dataset_id == dataset_id)
                )
            ).all()
        )
        if not examples:
            raise PermanentJobError("dataset is empty")
        dataset.example_count = len(examples)
        dataset.manifest_sha256 = _dataset_manifest(examples)
        if dataset.status == "generating":
            dataset.status = "review_required"
        await session.commit()
        return {"dataset_id": str(dataset.id), "manifest": dataset.manifest_sha256}


def _freesolo() -> FreeSoloCli:
    if not settings.freesolo_api_key:
        raise PermanentJobError("freesolo_not_configured: set FREESOLO_API_KEY")
    return FreeSoloCli(settings.freesolo_api_key, settings.freesolo_org)


def _write_rendered_config(run: TrainingRun) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", prefix=f"ecoroute-{run.id}-", delete=False
    )
    with handle:
        handle.write(run.rendered_config)
    return Path(handle.name)


def _parse_environment_id(value: str) -> str:
    payload = _parse_json_or_text(value)
    for key in ("environment_id", "environmentId", "id"):
        candidate = payload.get(key)
        if candidate and "/" in str(candidate):
            return str(candidate)[:300]
    match = re.search(r"\b([a-z0-9][a-z0-9-]{0,62}/[a-z0-9][a-z0-9-]{0,62})\b", value)
    if match:
        return match.group(1)
    raise PermanentJobError("FreeSOLO environment push did not return an environment ID")


async def _publish_training_environment(run_id: uuid.UUID) -> str:
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        if run is None:
            raise PermanentJobError("training run no longer exists")
        if run.freesolo_environment_id:
            return run.freesolo_environment_id
        dataset = await session.get(Dataset, run.dataset_id)
        if dataset is None or dataset.status != "approved":
            raise PermanentJobError("training dataset is not approved")
        examples = list(
            (
                await session.scalars(
                    select(DatasetExample).where(
                        DatasetExample.dataset_id == dataset.id,
                        DatasetExample.approved.is_(True),
                    )
                )
            ).all()
        )
        policies = (
            list(
                (
                    await session.scalars(
                        select(PolicyDocument).where(
                            PolicyDocument.slm_profile_id == run.slm_profile_id,
                            PolicyDocument.active.is_(True),
                        )
                    )
                ).all()
            )
            if run.slm_profile_id
            else []
        )
        dataset_version = dataset.version
        manifest = dataset.manifest_sha256 or "unversioned"
        kind = run.kind
    if not examples:
        raise PermanentJobError("approved training dataset has no accepted examples")
    source = ROOT / "training" / ("router" if kind == "router" else "support-slm")
    if not source.exists():
        raise PermanentJobError("training environment source is missing")
    name = (
        f"ecoroute-{'router' if kind == 'router' else 'support'}-v{dataset_version}-{manifest[:8]}"
    )
    with tempfile.TemporaryDirectory(prefix="ecoroute-freesolo-environment-") as temporary:
        environment_root = Path(temporary) / "environment"
        shutil.copytree(source, environment_root)
        dataset_dir = environment_root / "dataset"
        dataset_dir.mkdir(exist_ok=True)
        by_split: dict[str, list[str]] = {"train": [], "eval": [], "test": []}
        for example in examples:
            row = {
                "input": example.input,
                "output": json.dumps(example.output, sort_keys=True, separators=(",", ":")),
                "metadata": {
                    **example.example_metadata,
                    "id": example.external_id,
                    "approved": True,
                },
            }
            by_split.setdefault(example.split, []).append(
                json.dumps(row, sort_keys=True, separators=(",", ":"))
            )
        for split, rows in by_split.items():
            (dataset_dir / f"{split}.jsonl").write_text("\n".join(rows) + ("\n" if rows else ""))
        if policies:
            policy_dir = environment_root / "policies"
            policy_dir.mkdir(exist_ok=True)
            for existing in policy_dir.glob("*.txt"):
                existing.unlink()
            for policy in policies:
                (policy_dir / f"{policy.policy_key}.txt").write_text(policy.content.strip() + "\n")
        pushed = await _freesolo().run(
            build_command("env_push", str(environment_root), name=name), timeout_seconds=300
        )
    if pushed.returncode != 0:
        raise PermanentJobError(f"FreeSOLO environment push failed: {pushed.stderr[-2000:]}")
    environment_id = _parse_environment_id(pushed.stdout + "\n" + pushed.stderr)
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        assert run is not None
        if "${FREESOLO_ENVIRONMENT_ID}" not in run.rendered_config:
            raise PermanentJobError("rendered config is missing the environment placeholder")
        run.freesolo_environment_id = environment_id
        run.rendered_config = run.rendered_config.replace(
            "${FREESOLO_ENVIRONMENT_ID}", environment_id
        )
        await session.commit()
    return environment_id


def _parse_json_or_text(value: str) -> dict[str, Any]:
    for line in reversed(value.splitlines()):
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return {"text": value[-20_000:]}


def _parse_quote(value: str) -> Decimal:
    parsed = _parse_json_or_text(value)
    for key in ("cost_usd", "costUsd", "estimated_cost_usd", "estimatedCostUsd", "cost"):
        if key in parsed:
            return Decimal(str(parsed[key])).quantize(Decimal("0.000000001"))
    match = re.search(r"(?i)(?:estimated\s+)?cost[^0-9$]*\$?([0-9]+(?:\.[0-9]+)?)", value)
    if match:
        return Decimal(match.group(1)).quantize(Decimal("0.000000001"))
    raise PermanentJobError("FreeSOLO cost output did not contain a parseable quote")


def _parse_run_id(value: str) -> str:
    parsed = _parse_json_or_text(value)
    for key in ("run_id", "runId", "id"):
        if parsed.get(key):
            return str(parsed[key])[:300]
    match = re.search(r"(?i)\b(?:run(?:\s+id)?)[\s:=]+([A-Za-z0-9._:/-]+)", value)
    if match:
        return match.group(1)[:300]
    raise PermanentJobError("FreeSOLO launch output did not contain a run ID")


async def handle_training_validate(job: Job) -> dict[str, Any]:
    run_id = uuid.UUID(job.input["training_run_id"])
    environment_id = await _publish_training_environment(run_id)
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        if run is None:
            raise PermanentJobError("training run no longer exists")
        if run.status not in {"validating", "queued"}:
            raise PermanentJobError("training run is not validating")
        dataset = await session.get(Dataset, run.dataset_id)
        if dataset is None or dataset.status != "approved" or not dataset.manifest_sha256:
            raise PermanentJobError("training dataset is not approved and frozen")
        client = _freesolo()
        path = _write_rendered_config(run)
    try:
        dry = await client.run(build_command("train_dry_run", str(path)), timeout_seconds=120)
        if dry.returncode != 0:
            raise PermanentJobError(f"FreeSOLO dry-run failed: {dry.stderr[-2000:]}")
        quote_result = await client.run(build_command("train_cost", str(path)), timeout_seconds=120)
        if quote_result.returncode != 0:
            raise PermanentJobError(f"FreeSOLO quote failed: {quote_result.stderr[-2000:]}")
        quote = _parse_quote(quote_result.stdout + "\n" + quote_result.stderr)
    finally:
        path.unlink(missing_ok=True)
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        assert run is not None
        run.cost_quote_usd = quote
        run.status = "queued"
        await session.commit()
    await _training_event(
        run,
        "validated",
        {
            "status": "queued",
            "costQuoteUsd": str(quote),
            "dryRun": _parse_json_or_text(dry.stdout),
            "environmentId": environment_id,
        },
    )
    return {"training_run_id": str(run.id), "cost_quote_usd": str(quote), "status": "queued"}


async def _enqueue_poll(run: TrainingRun) -> None:
    key = f"training.poll:{run.id}:{run.freesolo_run_id}"
    async with SessionLocal() as session:
        existing = await session.scalar(select(Job).where(Job.idempotency_key == key))
        if existing is None:
            poll = Job(
                workspace_id=run.workspace_id,
                kind="training.poll",
                status="queued",
                idempotency_key=key,
                input={"training_run_id": str(run.id)},
                available_at=utcnow() + timedelta(seconds=15),
            )
            session.add(poll)
            await session.commit()


async def handle_training_launch(job: Job) -> dict[str, Any]:
    run_id = uuid.UUID(job.input["training_run_id"])
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        if run is None or run.status != "training":
            raise PermanentJobError("training run is not launchable")
        client = _freesolo()
        path = _write_rendered_config(run)
    try:
        result = await client.run(build_command("train_launch", str(path)), timeout_seconds=120)
    finally:
        path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"FreeSOLO launch failed: {result.stderr[-2000:]}")
    external_id = _parse_run_id(result.stdout + "\n" + result.stderr)
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        assert run is not None
        run.freesolo_run_id = external_id
        await session.commit()
    await _training_event(run, "launched", {"status": "training", "freesoloRunId": external_id})
    await _enqueue_poll(run)
    return {"training_run_id": str(run.id), "freesolo_run_id": external_id}


def _status_value(payload: dict[str, Any]) -> str:
    status = str(payload.get("status", payload.get("state", "unknown"))).casefold()
    if status != "unknown":
        return status
    match = re.search(
        r"(?im)^\s*(?:status|state)\s*[:=]\s*([a-z_-]+)", str(payload.get("text", ""))
    )
    return match.group(1).casefold() if match else "unknown"


async def handle_training_poll(job: Job) -> dict[str, Any] | DeferredJob:
    run_id = uuid.UUID(job.input["training_run_id"])
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        if run is None or not run.freesolo_run_id:
            raise PermanentJobError("training run has no FreeSOLO run ID")
        external_id = run.freesolo_run_id
    result = await _freesolo().run(build_command("status", external_id), timeout_seconds=60)
    if result.returncode != 0:
        raise RuntimeError(f"FreeSOLO status failed: {result.stderr[-2000:]}")
    payload = _parse_json_or_text(result.stdout)
    status = _status_value(payload)
    await _training_event(run, "status_snapshot", payload)
    if status in {"failed", "error"}:
        raise PermanentJobError(f"FreeSOLO run failed: {payload}")
    if status in {"cancelled", "canceled"}:
        async with SessionLocal() as session:
            current = await session.get(TrainingRun, run_id)
            assert current is not None
            current.status = "cancelled"
            current.completed_at = utcnow()
            await session.commit()
        return {"status": "cancelled"}
    if status in {"completed", "succeeded", "success", "finished", "done"}:
        metrics = payload.get("eval_metrics") or payload.get("metrics")
        async with SessionLocal() as session:
            current = await session.get(TrainingRun, run_id)
            assert current is not None
            current.status = "evaluating" if metrics else "completed"
            if isinstance(metrics, dict):
                current.eval_metrics = metrics
                current.status = "completed"
            current.completed_at = utcnow()
            await session.commit()
        return {"status": current.status, "metrics": metrics}
    return DeferredJob(15, {"status": status, "snapshot": payload})


async def handle_training_evaluate(job: Job) -> dict[str, Any]:
    run_id = uuid.UUID(job.input["training_run_id"])
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        if run is None or not run.freesolo_run_id:
            raise PermanentJobError("training run has no FreeSOLO run ID")
        external_id = run.freesolo_run_id
    result = await _freesolo().run(build_command("status", external_id), timeout_seconds=60)
    if result.returncode != 0:
        raise RuntimeError(f"FreeSOLO status failed: {result.stderr[-2000:]}")
    payload = _parse_json_or_text(result.stdout)
    metrics = payload.get("eval_metrics") or payload.get("metrics")
    if not isinstance(metrics, dict):
        raise PermanentJobError("FreeSOLO status did not include evaluation metrics")
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        assert run is not None
        run.eval_metrics = metrics
        run.status = "completed"
        run.completed_at = utcnow()
        await session.commit()
    await _training_event(run, "evaluated", {"status": "completed", "metrics": metrics})
    return {"training_run_id": str(run.id), "metrics": metrics}


def _parse_deployment(value: str) -> tuple[str, str]:
    payload = _parse_json_or_text(value)
    base_url = payload.get("base_url") or payload.get("baseUrl") or payload.get("url")
    model_id = payload.get("model_id") or payload.get("modelId") or payload.get("deployment_id")
    if not base_url or not model_id:
        raise PermanentJobError("FreeSOLO deploy output did not include base URL and model ID")
    normalized_url = str(base_url).rstrip("/")
    if not normalized_url.startswith(("http://", "https://")):
        raise PermanentJobError("FreeSOLO deploy output included an invalid base URL")
    if not normalized_url.endswith("/v1"):
        normalized_url += "/v1"
    return normalized_url, str(model_id)[:300]


async def handle_training_deploy(job: Job) -> dict[str, Any]:
    run_id = uuid.UUID(job.input["training_run_id"])
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        if run is None or not run.freesolo_run_id or run.status != "deploying":
            raise PermanentJobError("training run is not deployable")
        external_id = run.freesolo_run_id
    client = _freesolo()
    dry = await client.run(build_command("deploy_dry_run", external_id), timeout_seconds=120)
    if dry.returncode != 0:
        raise PermanentJobError(f"FreeSOLO deploy dry-run failed: {dry.stderr[-2000:]}")
    result = await client.run(build_command("deploy", external_id), timeout_seconds=300)
    if result.returncode != 0:
        raise RuntimeError(f"FreeSOLO deploy failed: {result.stderr[-2000:]}")
    base_url, model_id = _parse_deployment(result.stdout + "\n" + result.stderr)
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        assert run is not None
        run.deployment_base_url = base_url
        run.deployed_model_id = model_id
        run.status = "deployed"
        endpoint = await session.scalar(
            select(ModelEndpoint).where(
                ModelEndpoint.workspace_id == run.workspace_id,
                ModelEndpoint.provider == "freesolo",
                ModelEndpoint.physical_model == model_id,
                ModelEndpoint.deleted_at.is_(None),
            )
        )
        if endpoint is None:
            profile = (
                await session.get(SlmProfile, run.slm_profile_id) if run.slm_profile_id else None
            )
            endpoint = ModelEndpoint(
                workspace_id=run.workspace_id,
                name=(f"{profile.name} FreeSOLO" if profile else "FreeSOLO router")[:200],
                provider="freesolo",
                base_url=base_url,
                credential_ref="env:FREESOLO_API_KEY",
                physical_model=model_id,
                region=str(job.input.get("region", "unknown"))[:100],
                grid_zone=str(job.input.get("grid_zone", "unknown"))[:100],
                quality_tier="specialized" if profile else "small",
                capabilities=["text", "json_schema", "streaming"],
                context_window_tokens=4096,
                input_usd_per_million_tokens=Decimal("0"),
                output_usd_per_million_tokens=Decimal("0"),
                fixed_request_kwh=0.0,
                input_kwh_per_1k_tokens=0.0,
                output_kwh_per_1k_tokens=0.0,
                energy_evidence="estimated",
                latency_p50_ms=0,
                latency_p95_ms=0,
                self_hosted=False,
                slm_profile_id=run.slm_profile_id,
                enabled=True,
                health_state="unknown",
                coefficient_version="unconfigured-v1",
            )
            session.add(endpoint)
            await session.flush()
        else:
            endpoint.base_url = base_url
            endpoint.slm_profile_id = run.slm_profile_id
            endpoint.version += 1
        if run.slm_profile_id:
            profile = await session.get(SlmProfile, run.slm_profile_id)
            if profile is not None:
                profile.status = "experimental" if job.input.get("experimental") else "ready"
                profile.active_model_endpoint_id = endpoint.id
        await session.commit()
    await _training_event(
        run,
        "deployed",
        {
            "status": "deployed",
            "baseUrl": base_url,
            "modelId": model_id,
            "endpointId": str(endpoint.id),
        },
    )
    return {
        "training_run_id": str(run.id),
        "base_url": base_url,
        "model_id": model_id,
        "endpoint_id": str(endpoint.id),
    }


async def handle_training_export(job: Job) -> dict[str, Any]:
    run_id = uuid.UUID(job.input["training_run_id"])
    repository = str(job.input["repository"])
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        if run is None or not run.freesolo_run_id:
            raise PermanentJobError("training run has no adapter ID")
        external_id = run.freesolo_run_id
    result = await _freesolo().run(
        build_command("export", external_id, repository), timeout_seconds=600
    )
    if result.returncode != 0:
        raise RuntimeError(f"FreeSOLO export failed: {result.stderr[-2000:]}")
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        assert run is not None
        run.status = "exported"
        await session.commit()
    await _training_event(run, "exported", {"status": "exported", "repository": repository})
    return {"training_run_id": str(run.id), "repository": repository}


async def handle_training_cancel(job: Job) -> dict[str, Any]:
    """Cancel the managed run through the now-documented Flash cancellation command."""
    run_id = uuid.UUID(job.input["training_run_id"])
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        if run is None:
            raise PermanentJobError("training run no longer exists")
        external_id = run.freesolo_run_id
    if external_id:
        cancelled = await _freesolo().run(build_command("cancel", external_id), timeout_seconds=300)
        if cancelled.returncode != 0:
            raise RuntimeError(f"FreeSOLO cancellation failed: {cancelled.stderr[-2000:]}")
    async with SessionLocal() as session:
        run = await session.get(TrainingRun, run_id)
        assert run is not None
        run.status = "cancelled"
        run.completed_at = utcnow()
        poll_jobs = list(
            (
                await session.scalars(
                    select(Job).where(
                        Job.kind == "training.poll",
                        Job.input["training_run_id"].astext == str(run.id),
                        Job.status.in_(["queued", "running"]),
                    )
                )
            ).all()
        )
        for poll in poll_jobs:
            poll.status = "cancelled"
            poll.completed_at = utcnow()
        await session.commit()
    await _training_event(
        run,
        "cancelled",
        {
            "status": "cancelled",
            "scope": "FreeSOLO managed run and EcoRoute polling",
        },
    )
    return {"training_run_id": str(run.id), "status": "cancelled"}


def _credential_is_configured(reference: str | None) -> bool:
    if not reference:
        return True
    variable = reference.removeprefix("env:")
    configured = {
        "FREESOLO_API_KEY": settings.freesolo_api_key,
        "GEMINI_API_KEY": settings.gemini_api_key,
        "OPENAI_API_KEY": settings.openai_api_key,
    }
    return bool(configured.get(variable) or os.environ.get(variable))


async def handle_endpoint_health(job: Job) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    async with SessionLocal() as session:
        endpoints = list(
            (
                await session.scalars(
                    select(ModelEndpoint).where(
                        ModelEndpoint.enabled.is_(True),
                        ModelEndpoint.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        for endpoint in endpoints:
            endpoint.last_health_at = utcnow()
            success_key = f"ecoroute:health:{endpoint.id}:success"
            failure_key = f"ecoroute:health:{endpoint.id}:failure"
            if not _credential_is_configured(endpoint.credential_ref):
                endpoint.health_state = "unknown"
                endpoint.last_health_error = "Credential is not configured"
                await redis_client.delete(success_key, failure_key)
                checked.append({"endpointId": str(endpoint.id), "state": "unknown"})
                continue
            try:
                result = await providers.for_provider(endpoint.provider).health(endpoint)
                latency_ms = int(result.get("latencyMs", 0))
                passed = result.get("status") == "healthy"
                if endpoint.latency_p95_ms > 0 and latency_ms > endpoint.latency_p95_ms:
                    passed = False
                    result["message"] = "Health latency exceeded the configured p95 SLO"
                if passed:
                    streak = int(await redis_client.incr(success_key))
                    await redis_client.expire(success_key, 300)
                    await redis_client.delete(failure_key)
                    endpoint.health_state = "healthy" if streak >= 2 else "degraded"
                    endpoint.last_health_error = None
                else:
                    streak = int(await redis_client.incr(failure_key))
                    await redis_client.expire(failure_key, 300)
                    await redis_client.delete(success_key)
                    endpoint.health_state = "unhealthy" if streak >= 2 else "degraded"
                    endpoint.last_health_error = str(
                        result.get("message", "Provider health check failed")
                    )[:2000]
            except Exception as exc:
                streak = int(await redis_client.incr(failure_key))
                await redis_client.expire(failure_key, 300)
                await redis_client.delete(success_key)
                endpoint.health_state = "unhealthy" if streak >= 2 else "degraded"
                endpoint.last_health_error = f"{type(exc).__name__}: health check failed"[:2000]
            checked.append({"endpointId": str(endpoint.id), "state": endpoint.health_state})
        await session.commit()
    return {"checked": checked}


async def handle_carbon_refresh(job: Job) -> dict[str, Any]:
    zones = [str(value) for value in job.input.get("zones", [])]
    scenario_raw = await redis_client.get("ecoroute:demo:grid")
    scenario = scenario_raw.decode() if isinstance(scenario_raw, bytes) else scenario_raw
    provider = (
        FixtureCarbonProvider(scenario or "moderate")
        if settings.demo_mode
        else configured_carbon_provider(settings)
    )
    stored = []
    failures: list[dict[str, str]] = []
    async with SessionLocal() as session:
        locations: list[tuple[str, str | None, str | None]]
        if zones:
            locations = [(zone, None, None) for zone in zones]
        else:
            endpoints = list(
                (
                    await session.scalars(
                        select(ModelEndpoint).where(
                            ModelEndpoint.enabled.is_(True),
                            ModelEndpoint.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
            locations = list(
                dict.fromkeys(
                    (
                        endpoint.grid_zone,
                        endpoint.grid_data_center_provider
                        if endpoint.grid_lookup_mode == "data_center"
                        else None,
                        endpoint.grid_data_center_region
                        if endpoint.grid_lookup_mode == "data_center"
                        else None,
                    )
                    for endpoint in endpoints
                )
            )
        for zone, data_center_provider, data_center_region in locations:
            lookup_key = carbon_lookup_key(data_center_provider, data_center_region)
            try:
                reading = await provider.reading(
                    zone,
                    data_center_provider=data_center_provider,
                    data_center_region=data_center_region,
                )
            except (TimeoutError, TypeError, OSError, ValueError, httpx.HTTPError) as exc:
                failures.append(
                    {
                        "zone": zone,
                        "error": type(exc).__name__,
                    }
                )
                continue
            if utcnow() - reading.observed_at > timedelta(
                minutes=settings.carbon_freshness_target_minutes
            ):
                reading = reading.model_copy(update={"evidence": "stale"})
            existing = await session.scalar(
                select(CarbonReadingRecord).where(
                    CarbonReadingRecord.zone == reading.zone,
                    CarbonReadingRecord.observed_at == reading.observed_at,
                    CarbonReadingRecord.source == reading.source,
                    CarbonReadingRecord.lookup_key == lookup_key,
                )
            )
            if existing is None:
                session.add(
                    CarbonReadingRecord(
                        zone=reading.zone,
                        intensity_gco2_kwh=reading.intensity_gco2_kwh,
                        observed_at=reading.observed_at,
                        fetched_at=reading.fetched_at,
                        source=reading.source,
                        evidence=reading.evidence,
                        lookup_key=lookup_key,
                        reading_metadata=reading.metadata,
                    )
                )
            await redis_client.set(
                carbon_cache_key(zone, data_center_provider, data_center_region),
                reading.model_dump_json(),
                ex=settings.carbon_cache_seconds,
            )
            stored.append(reading.model_dump(mode="json"))
        await session.commit()
    if locations and not stored:
        raise RuntimeError("All configured carbon locations failed to refresh")
    refreshed_zones = [location[0] for location in locations]
    await _publish_job_event(job, "carbon.updated", {"zones": refreshed_zones})
    return {"readings": stored, "failures": failures}


async def handle_cache_cleanup(job: Job) -> dict[str, Any]:
    now = utcnow()
    async with SessionLocal() as session:
        expired = list(
            (await session.scalars(select(CacheEntry).where(CacheEntry.expires_at <= now))).all()
        )
        invalidated = list(
            (
                await session.scalars(
                    select(CacheEntry).where(
                        CacheEntry.invalidated_at.is_not(None), CacheEntry.expires_at > now
                    )
                )
            ).all()
        )
        for entry in [*expired, *invalidated]:
            await redis_client.delete(
                f"ecoroute:exact:{entry.workspace_id}:{entry.exact_fingerprint}"
            )
        # Expiration is lifecycle cleanup. Explicit invalidation remains in
        # PostgreSQL for audit until the retention job reaches its boundary.
        await session.execute(delete(CacheEntry).where(CacheEntry.expires_at <= now))
        active_count = int(
            await session.scalar(
                select(func.count(CacheEntry.id)).where(CacheEntry.invalidated_at.is_(None))
            )
            or 0
        )
        evicted = 0
        if active_count > settings.cache_max_entries:
            overflow = active_count - settings.cache_max_entries
            oldest = list(
                (
                    await session.scalars(
                        select(CacheEntry)
                        .where(CacheEntry.invalidated_at.is_(None))
                        .order_by(CacheEntry.last_hit_at.asc().nullsfirst(), CacheEntry.created_at)
                        .limit(overflow)
                    )
                ).all()
            )
            for entry in oldest:
                await redis_client.delete(
                    f"ecoroute:exact:{entry.workspace_id}:{entry.exact_fingerprint}"
                )
                entry.invalidated_at = now
                evicted += 1
        await session.commit()
    return {
        "expired": len(expired),
        "invalidated_retained": len(invalidated) + evicted,
        "capacity_evicted": evicted,
    }


async def handle_retention_cleanup(job: Job) -> dict[str, Any]:
    telemetry_before = utcnow() - timedelta(days=settings.telemetry_retention_days)
    requests_before = utcnow() - timedelta(days=settings.request_retention_days)
    cache_before = utcnow() - timedelta(days=settings.request_retention_days)
    async with SessionLocal() as session:
        telemetry_result = await session.execute(
            delete(TelemetrySample).where(TelemetrySample.observed_at < telemetry_before)
        )
        # Request rows have dependent audit evidence and are retained as a unit. Redacted previews
        # and client correlation metadata are removed at the configured boundary.
        old_requests = list(
            (
                await session.scalars(
                    select(GatewayRequest).where(GatewayRequest.started_at < requests_before)
                )
            ).all()
        )
        for request in old_requests:
            request.redacted_prompt_preview = None
            request.client_metadata = {}
            request.raw_prompt_encrypted = None
        cache_result = await session.execute(
            delete(CacheEntry).where(
                CacheEntry.invalidated_at.is_not(None),
                CacheEntry.invalidated_at < cache_before,
            )
        )
        await session.commit()
    return {
        "telemetry_deleted": int(getattr(telemetry_result, "rowcount", 0)),
        "requests_minimized": len(old_requests),
        "invalidated_cache_deleted": int(getattr(cache_result, "rowcount", 0)),
    }


async def handle_report_generate(job: Job) -> dict[str, Any]:
    async with SessionLocal() as session:
        count = int(await session.scalar(select(func.count(GatewayRequest.id))) or 0)
    return {
        "generated_at": utcnow().isoformat(),
        "request_count": count,
        "filters": job.input.get("filters", {}),
        "methodology_version": "ecoroute-v2",
    }


def _simulated_benchmark_metrics(seed: int, optimized: bool) -> dict[str, float]:
    randomizer = random.Random(seed + (1 if optimized else 0))
    throughput = 8.0 + randomizer.random()
    latency = 118.0 + randomizer.random() * 8
    energy = 0.00125 + randomizer.random() * 0.00008
    if optimized:
        throughput *= 0.98
        latency *= 1.03
        energy *= 0.78
    return {
        "successful_throughput_rps": round(throughput, 4),
        "p50_latency_ms": round(latency, 3),
        "p95_latency_ms": round(latency * 1.42, 3),
        "energy_per_request_kwh": round(energy, 9),
        "energy_per_token_kwh": round(energy / 128, 12),
        "quality_score": round(0.94 + randomizer.random() * 0.02, 4),
    }


async def handle_demo_load(job: Job) -> dict[str, Any] | DeferredJob:
    benchmark_id = job.input.get("benchmark_id")
    if benchmark_id:
        async with SessionLocal() as session:
            benchmark = await session.get(Benchmark, uuid.UUID(str(benchmark_id)))
            if benchmark is None:
                raise PermanentJobError("benchmark no longer exists")
            agent = await session.get(NodeAgent, benchmark.agent_id)
            if agent is None:
                raise PermanentJobError("benchmark agent no longer exists")
            if not agent.capabilities.get("simulator"):
                if benchmark.status == "completed":
                    return {
                        "benchmark_id": str(benchmark.id),
                        "comparison": benchmark.comparison or {},
                    }
                if benchmark.status == "cancelled":
                    return {"benchmark_id": str(benchmark.id), "status": "cancelled"}
                if benchmark.status == "failed":
                    return {"benchmark_id": str(benchmark.id), "status": "failed"}
                if benchmark.status == "queued":
                    benchmark.status = "assigned"
                    await session.commit()
                    await _publish_job_event(
                        job,
                        "benchmark.status",
                        {
                            "benchmarkId": str(benchmark.id),
                            "phase": "assigned",
                            "status": "assigned",
                        },
                    )
                return DeferredJob(
                    delay_seconds=5,
                    output={
                        "benchmark_id": str(benchmark.id),
                        "status": benchmark.status,
                        "runner": "node-agent",
                    },
                )
            benchmark.status = "running"
            await session.commit()
        await _publish_job_event(
            job, "benchmark.status", {"benchmarkId": str(benchmark.id), "phase": "warmup"}
        )
        seed = settings.simulator_seed + int(benchmark.id.int % 100_000)
        baseline = _simulated_benchmark_metrics(seed, False)
        optimized = _simulated_benchmark_metrics(seed, True)
        comparison = {
            "throughputChangePct": round(
                (optimized["successful_throughput_rps"] / baseline["successful_throughput_rps"] - 1)
                * 100,
                3,
            ),
            "p95LatencyChangePct": round(
                (optimized["p95_latency_ms"] / baseline["p95_latency_ms"] - 1) * 100, 3
            ),
            "energyPerRequestChangePct": round(
                (optimized["energy_per_request_kwh"] / baseline["energy_per_request_kwh"] - 1)
                * 100,
                3,
            ),
            "qualityChange": round(optimized["quality_score"] - baseline["quality_score"], 4),
        }
        async with SessionLocal() as session:
            benchmark = await session.get(Benchmark, uuid.UUID(str(benchmark_id)))
            assert benchmark is not None
            if benchmark.status == "cancelled":
                return {"benchmark_id": str(benchmark.id), "status": "cancelled"}
            benchmark.baseline_metrics = baseline
            benchmark.optimized_metrics = optimized
            benchmark.comparison = comparison
            benchmark.status = "completed"
            benchmark.completed_at = utcnow()
            agent = await session.get(NodeAgent, benchmark.agent_id)
            if agent is not None:
                agent.desired_profile = "observe"
                agent.active_profile = "observe"
                agent.desired_state_version += 1
            await session.commit()
        await _publish_job_event(
            job,
            "benchmark.status",
            {"benchmarkId": str(benchmark.id), "phase": "completed", "status": "completed"},
        )
        return {"benchmark_id": str(benchmark.id), "comparison": comparison}

    count = int(job.input.get("request_count", 20))
    concurrency = int(job.input.get("concurrency", 2))
    model = str(job.input.get("model", "support-default"))
    prompts = [
        "What is the return window?",
        "My order is late. What should I do?",
        "Can I exchange an item that is out of stock?",
        "Summarize the shipping policy.",
    ]
    semaphore = __import__("asyncio").Semaphore(concurrency)

    async def send(index: int) -> int:
        async with semaphore:
            async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
                response = await client.post(
                    f"{settings.gateway_internal_url.rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.gateway_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompts[index % len(prompts)]}],
                        "temperature": 0,
                        "metadata": {"client_app": "demo-load"},
                    },
                )
                return response.status_code

    import asyncio

    statuses = await asyncio.gather(*(send(index) for index in range(count)))
    return {
        "requested": count,
        "completed": len(statuses),
        "successful": sum(status < 400 for status in statuses),
    }


HANDLERS: dict[str, Handler] = {
    "dataset.generate": handle_dataset_generate,
    "dataset.finalize": handle_dataset_finalize,
    "training.validate": handle_training_validate,
    "training.launch": handle_training_launch,
    "training.poll": handle_training_poll,
    "training.evaluate": handle_training_evaluate,
    "training.deploy": handle_training_deploy,
    "training.export": handle_training_export,
    "training.cancel": handle_training_cancel,
    "endpoint.health": handle_endpoint_health,
    "carbon.refresh": handle_carbon_refresh,
    "cache.cleanup": handle_cache_cleanup,
    "retention.cleanup": handle_retention_cleanup,
    "report.generate": handle_report_generate,
    "demo.load": handle_demo_load,
}
