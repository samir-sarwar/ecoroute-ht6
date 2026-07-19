from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from ecoroute.api.events import publish_event
from ecoroute.config import get_settings
from ecoroute.db.base import utcnow
from ecoroute.db.models import Dataset, Job, TrainingRun, Workspace
from ecoroute.db.session import SessionLocal, redis_client
from redis.asyncio import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import or_, select

from ecoroute_worker.jobs.handlers import HANDLERS, DeferredJob, PermanentJobError

settings = get_settings()
GROUP = "ecoroute-workers"
CONSUMER = f"worker-{uuid.uuid4().hex[:8]}"
RETRY_SECONDS = (15, 60, 300)


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


async def _set_domain_failure(job: Job, message: str) -> None:
    async with SessionLocal() as session:
        if job.kind.startswith("dataset.") and job.input.get("dataset_id"):
            dataset = await session.get(Dataset, uuid.UUID(job.input["dataset_id"]))
            if dataset is not None and dataset.status not in {"approved"}:
                dataset.status = "failed"
        if job.kind.startswith("training.") and job.input.get("training_run_id"):
            run = await session.get(TrainingRun, uuid.UUID(job.input["training_run_id"]))
            if run is not None and run.status not in {"cancelled", "deployed", "exported"}:
                run.status = "failed"
                run.error_code = message.split(":", 1)[0][:100]
                run.error_message = message[:10_000]
        await session.commit()


async def _finish_job(job_id: uuid.UUID, output: dict[str, Any]) -> Job | None:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return None
        job.output = output
        job.status = "completed"
        job.error = None
        job.locked_at = None
        job.completed_at = utcnow()
        await session.commit()
        return job


async def _defer_job(job_id: uuid.UUID, deferred: DeferredJob) -> None:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.output = deferred.output
        job.status = "queued"
        job.locked_at = None
        job.available_at = utcnow() + timedelta(seconds=deferred.delay_seconds)
        await session.commit()


async def _fail_or_retry(job_id: uuid.UUID, exc: Exception, permanent: bool) -> Job | None:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return None
        message = f"{type(exc).__name__}: {str(exc)[:9000]}"
        job.error = message
        job.locked_at = None
        if not permanent and job.attempts <= len(RETRY_SECONDS):
            delay = RETRY_SECONDS[job.attempts - 1]
            job.status = "queued"
            job.available_at = utcnow() + timedelta(seconds=delay)
        else:
            job.status = "failed"
            job.completed_at = utcnow()
        await session.commit()
        return job


async def execute_job(job_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        job = await session.scalar(
            select(Job).where(Job.id == job_id).with_for_update(skip_locked=True)
        )
        if job is None or job.status in {"completed", "cancelled", "failed"}:
            return
        if job.available_at > utcnow():
            job.locked_at = None
            await session.commit()
            return
        job.status = "running"
        job.locked_at = utcnow()
        job.attempts += 1
        await session.commit()
        kind = job.kind
        workspace_id = job.workspace_id
    handler = HANDLERS.get(kind)
    if handler is None:
        exc = PermanentJobError(f"unsupported job kind: {kind}")
        failed = await _fail_or_retry(job_id, exc, True)
        if failed is not None:
            await _set_domain_failure(failed, str(exc))
        return
    try:
        async with SessionLocal() as session:
            current = await session.get(Job, job_id)
            assert current is not None
            result = await handler(current)
        if isinstance(result, DeferredJob):
            await _defer_job(job_id, result)
            return
        finished = await _finish_job(job_id, result)
        if finished is not None:
            event_type = (
                "training.status"
                if kind.startswith(("training.", "dataset."))
                else "benchmark.status"
                if kind == "demo.load" and finished.input.get("benchmark_id")
                else "carbon.updated"
                if kind == "carbon.refresh"
                else "cache.invalidated"
                if kind == "cache.cleanup"
                else "endpoint.health"
                if kind == "endpoint.health"
                else "route.completed"
            )
            await publish_event(
                redis_client,
                settings,
                workspace_id,
                event_type,
                {"jobId": str(job_id), "kind": kind, "status": "completed"},
            )
    except Exception as exc:
        permanent = isinstance(exc, (PermanentJobError, ValueError))
        failed = await _fail_or_retry(job_id, exc, permanent)
        if failed is not None and failed.status == "failed":
            await _set_domain_failure(failed, str(exc))
            await publish_event(
                redis_client,
                settings,
                failed.workspace_id,
                "training.status" if kind.startswith(("training.", "dataset.")) else "route.failed",
                {
                    "jobId": str(job_id),
                    "kind": kind,
                    "status": "failed",
                    "errorCode": str(exc).split(":", 1)[0][:100],
                },
            )


async def _enqueue_due_jobs() -> int:
    stale = utcnow() - timedelta(minutes=5)
    async with SessionLocal() as session:
        jobs = list(
            (
                await session.scalars(
                    select(Job)
                    .where(
                        Job.status == "queued",
                        Job.available_at <= utcnow(),
                        or_(Job.locked_at.is_(None), Job.locked_at < stale),
                    )
                    .order_by(Job.available_at)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for job in jobs:
            job.locked_at = utcnow()
        await session.commit()
    for job in jobs:
        await redis_client.xadd("ecoroute:jobs", {"job_id": str(job.id)})
    return len(jobs)


async def _ensure_periodic_jobs() -> None:
    now = utcnow()
    schedules: tuple[tuple[str, int, dict[str, Any]], ...] = (
        ("endpoint.health", 30, {}),
        ("carbon.refresh", 120, {}),
        ("cache.cleanup", 300, {}),
        ("retention.cleanup", 3600, {}),
    )
    async with SessionLocal() as session:
        workspace = await session.scalar(select(Workspace).limit(1))
        if workspace is None:
            return
        for kind, seconds, payload in schedules:
            bucket = int(now.timestamp()) // seconds
            key = f"periodic:{kind}:{bucket}"
            if await session.scalar(select(Job.id).where(Job.idempotency_key == key)) is None:
                session.add(
                    Job(
                        workspace_id=workspace.id,
                        kind=kind,
                        status="queued",
                        idempotency_key=key,
                        input=payload,
                        available_at=now,
                    )
                )
        await session.commit()


async def _recover_pending() -> None:
    try:
        claimed: Any = await redis_client.xautoclaim(
            "ecoroute:jobs", GROUP, CONSUMER, min_idle_time=60_000, start_id="0-0", count=100
        )
    except ResponseError:
        return
    entries = claimed[1] if isinstance(claimed, (tuple, list)) and len(claimed) > 1 else []
    for message_id, fields in entries:
        raw_job_id = fields.get("job_id") or fields.get(b"job_id")
        if raw_job_id:
            await execute_job(uuid.UUID(_text(raw_job_id)))
        await redis_client.xack("ecoroute:jobs", GROUP, message_id)


async def run() -> None:
    try:
        await redis_client.xgroup_create("ecoroute:jobs", GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    iteration = 0
    while True:
        if iteration % 12 == 0:
            await _ensure_periodic_jobs()
        await _enqueue_due_jobs()
        if iteration % 6 == 0:
            await _recover_pending()
        try:
            messages: Any = await redis_client.xreadgroup(
                GROUP, CONSUMER, {"ecoroute:jobs": ">"}, count=10, block=5_000
            )
        except RedisTimeoutError:
            # An idle blocking read is normal and must not restart the worker.
            messages = []
        for stream, entries in messages:
            for message_id, fields in entries:
                raw_job_id = fields.get("job_id") or fields.get(b"job_id")
                if raw_job_id:
                    await execute_job(uuid.UUID(_text(raw_job_id)))
                await redis_client.xack(stream, GROUP, message_id)
        iteration += 1


if __name__ == "__main__":
    asyncio.run(run())
