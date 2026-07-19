from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import platform
import signal
import socket
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil
from ecoroute.db.base import uuid7

from ecoroute_agent.collectors import (
    LibreHardwareMonitorPowerSampler,
    SystemCollector,
    detect_capabilities,
)
from ecoroute_agent.controls import (
    CgroupV2Control,
    ControlTransaction,
    GatewayConcurrencyControl,
    NapiControl,
    NiceIoniceControl,
    NvmlPowerLimitControl,
    SchedExtControl,
)


def load_agent_id(path: Path) -> uuid.UUID:
    if path.exists():
        return uuid.UUID(path.read_text().strip())
    value = uuid7()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(str(value))
    path.chmod(0o600)
    return value


def _integer_list(name: str) -> list[int]:
    return [int(value) for value in os.getenv(name, "").split(",") if value.strip()]


def _local_approved() -> set[str]:
    return {
        value.strip()
        for value in os.getenv("ECOROUTE_AGENT_APPROVED_CONTROLS", "gateway_concurrency").split(",")
        if value.strip()
    }


def _build_controls(approved: set[str], capabilities: dict[str, bool]) -> list[Any]:
    controls: list[Any] = []
    if "gateway_concurrency" in approved:
        controls.append(
            GatewayConcurrencyControl(int(os.getenv("ECOROUTE_GATEWAY_BASELINE_CONCURRENCY", "16")))
        )
    if "cgroups_v2" in approved and capabilities.get("cgroups_v2"):
        controls.append(
            CgroupV2Control(
                Path(os.getenv("ECOROUTE_CGROUP_ROOT", "/sys/fs/cgroup/ecoroute.slice")),
                _integer_list("ECOROUTE_INFERENCE_PIDS"),
                _integer_list("ECOROUTE_BACKGROUND_PIDS"),
                os.getenv("ECOROUTE_ALLOW_HARD_CPU_QUOTA", "false").lower() == "true",
            )
        )
    if "nice_ionice" in approved and capabilities.get("nice_ionice"):
        controls.append(NiceIoniceControl(_integer_list("ECOROUTE_BACKGROUND_PIDS")))
    if "nvml_power_limit" in approved and capabilities.get("nvml_power_limit"):
        controls.append(NvmlPowerLimitControl())
    if "sched_ext" in approved and capabilities.get("sched_ext"):
        controls.append(
            SchedExtControl(
                Path(os.environ["ECOROUTE_SCHED_EXT_BINARY"]),
                json.loads(os.getenv("ECOROUTE_SCHED_EXT_ARGS", "[]")),
                os.environ["ECOROUTE_SCHED_EXT_SHA256"],
            )
        )
    if "napi_netdev_genl" in approved and capabilities.get("napi_netdev_genl"):
        if os.getenv("ECOROUTE_NAPI_BUSY_POLL_CONFIRMED", "false").lower() == "true":
            controls.append(
                NapiControl(
                    Path(os.environ["ECOROUTE_NAPI_YNL_HELPER"]),
                    [
                        value.strip()
                        for value in os.environ["ECOROUTE_NAPI_INTERFACES"].split(",")
                        if value.strip()
                    ],
                    json.loads(os.environ["ECOROUTE_NAPI_VALUES"]),
                )
            )
    return controls


def _outside_guardrail(profile: str, payload: dict[str, Any]) -> bool:
    current = payload.get("current") or {}
    baseline = payload.get("baseline") or {}
    latency_limit = 1.15 if profile == "balanced" else 1.30
    throughput_limit = 0.90 if profile == "balanced" else 0.75
    p95 = current.get("p95LatencyMs")
    baseline_p95 = baseline.get("p95_latency_ms", baseline.get("p95LatencyMs"))
    if p95 is not None and baseline_p95 and float(p95) > float(baseline_p95) * latency_limit:
        return True
    error = current.get("errorRate")
    baseline_error = baseline.get("error_rate", baseline.get("errorRate"))
    if (
        error is not None
        and baseline_error is not None
        and float(error) > float(baseline_error) + 0.005
    ):
        return True
    throughput = current.get("throughputRps")
    baseline_throughput = baseline.get("successful_throughput_rps", baseline.get("throughputRps"))
    return bool(
        throughput is not None
        and baseline_throughput
        and float(throughput) < float(baseline_throughput) * throughput_limit
    )


def _energy_counter_kwh(sample: dict[str, Any]) -> float | None:
    values: list[float] = []
    rapl = sample.get("rapl_energy_uj")
    if rapl is not None:
        values.append(float(rapl) / 3_600_000_000)
    gpu_total_mj = sum(
        float(device["total_energy_mj"])
        for device in sample.get("gpu", [])
        if device.get("total_energy_mj") is not None
    )
    if gpu_total_mj:
        values.append(gpu_total_mj / 3_600_000_000)
    return sum(values) if values else None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * percentile) - 1))
    return ordered[index]


def _process_cpu_seconds(pids: list[int]) -> float:
    total = 0.0
    for pid in pids:
        try:
            times = psutil.Process(pid).cpu_times()
        except (psutil.Error, OSError):
            continue
        total += float(times.user) + float(times.system)
    return total


def _cgroup_cpu_stat(group: str) -> dict[str, int]:
    root = Path(os.getenv("ECOROUTE_CGROUP_ROOT", "/sys/fs/cgroup/ecoroute.slice"))
    path = root / group / "cpu.stat"
    try:
        values = {}
        for line in path.read_text().splitlines():
            key, raw_value = line.split(maxsplit=1)
            values[key] = int(raw_value)
        return values
    except (OSError, ValueError):
        return {}


def _counter_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {
        key: max(0, value - before.get(key, 0))
        for key, value in after.items()
        if key in before
    }


async def _grid_carbon_reading(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    zone = os.getenv("ECOROUTE_BENCHMARK_GRID_ZONE", "").strip()
    if not zone:
        return None
    try:
        response = await client.get(f"{base_url}/api/v1/carbon/zones", headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    for item in payload.get("items", []):
        if str(item.get("zone", "")).casefold() != zone.casefold():
            continue
        intensity = item.get("intensityGco2Kwh", item.get("intensity_gco2_kwh"))
        try:
            value = float(intensity)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0:
            return {
                "zone": zone,
                "intensity_gco2_kwh": value,
                "source": item.get("source"),
                "evidence": item.get("evidence"),
                "observed_at": item.get("observedAt", item.get("observed_at")),
            }
    return None


async def _post_benchmark_sample(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    prompt_id: str,
) -> httpx.Response:
    for attempt in range(3):
        try:
            return await client.post(url, headers=headers, json={"promptId": prompt_id})
        except httpx.TransportError:
            if attempt == 2:
                raise
            await asyncio.sleep(0.1 * (attempt + 1))
    raise RuntimeError("unreachable benchmark retry state")


async def _benchmark_phase(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    agent_id: uuid.UUID,
    benchmark_id: str,
    duration_seconds: int,
    concurrency: int,
    collector: SystemCollector,
    prompt_ids: tuple[str, ...],
) -> dict[str, Any]:
    lhm_url = os.getenv("ECOROUTE_LHM_URL", "").strip()
    lhm_sampler = (
        LibreHardwareMonitorPowerSampler(
            lhm_url,
            interval_seconds=float(os.getenv("ECOROUTE_LHM_SAMPLE_SECONDS", "0.5")),
        )
        if lhm_url
        else None
    )
    lhm_stop = asyncio.Event()
    lhm_task = asyncio.create_task(lhm_sampler.run(lhm_stop)) if lhm_sampler else None
    before_sample = collector.sample()
    before = _energy_counter_kwh(before_sample)
    process_pids = {
        "inference": _integer_list("ECOROUTE_INFERENCE_PIDS"),
        "background": _integer_list("ECOROUTE_BACKGROUND_PIDS"),
    }
    before_process_cpu = {
        name: _process_cpu_seconds(pids) for name, pids in process_pids.items()
    }
    before_cgroup_cpu = {
        name: _cgroup_cpu_stat(name) for name in ("inference", "background")
    }
    started = asyncio.get_running_loop().time()
    results: list[dict[str, Any]] = []
    batch_index = 0
    try:
        while asyncio.get_running_loop().time() - started < duration_seconds:
            responses = await asyncio.gather(
                *(
                    _post_benchmark_sample(
                        client,
                        f"{base_url}/api/v1/agents/{agent_id}/benchmarks/{benchmark_id}/sample",
                        headers,
                        prompt_ids[(batch_index + index) % len(prompt_ids)],
                    )
                    for index in range(concurrency)
                )
            )
            for response in responses:
                response.raise_for_status()
                results.append(response.json())
            batch_index += concurrency
    finally:
        if lhm_task is not None:
            lhm_stop.set()
            try:
                await lhm_task
            except Exception as exc:  # The benchmark remains valid without optional energy data.
                lhm_sampler.last_error = f"{type(exc).__name__}: {exc}"
    elapsed = max(0.001, asyncio.get_running_loop().time() - started)
    after_sample = collector.sample()
    after = _energy_counter_kwh(after_sample)
    counter_energy = max(0.0, after - before) if before is not None and after is not None else None
    lhm_energy = lhm_sampler.energy_kwh if lhm_sampler else None
    energy = counter_energy if counter_energy is not None else lhm_energy
    process_cpu = {
        name: round(max(0.0, _process_cpu_seconds(pids) - before_process_cpu[name]), 6)
        for name, pids in process_pids.items()
    }
    cgroup_cpu = {
        name: _counter_delta(before_cgroup_cpu[name], _cgroup_cpu_stat(name))
        for name in ("inference", "background")
    }
    energy_sources = []
    if after_sample.get("rapl_energy_uj") is not None:
        energy_sources.append("rapl")
    if any(device.get("total_energy_mj") is not None for device in after_sample.get("gpu", [])):
        energy_sources.append("nvml-total-energy")
    if counter_energy is None and lhm_energy is not None:
        energy_sources.append("libre-hardware-monitor:cpu-package")
    successful = [item for item in results if item.get("success")]
    latencies = [float(item.get("latencyMs", 0)) for item in successful]
    total_tokens = sum(
        int(item.get("inputTokens", 0)) + int(item.get("outputTokens", 0)) for item in successful
    )
    quality = (
        sum(float(item.get("qualityScore", 0)) for item in results) / len(results)
        if results
        else 0.0
    )
    carbon_reading = await _grid_carbon_reading(client, base_url, headers)
    carbon_grams = (
        energy * carbon_reading["intensity_gco2_kwh"]
        if energy is not None and carbon_reading is not None
        else None
    )
    return {
        "successful_throughput_rps": round(len(successful) / elapsed, 4),
        "p50_latency_ms": round(_percentile(latencies, 0.50), 3),
        "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
        "energy_per_request_kwh": round(energy / len(successful), 12)
        if energy is not None and successful
        else None,
        "energy_per_token_kwh": round(energy / total_tokens, 15)
        if energy is not None and total_tokens
        else None,
        "energy_kwh": round(energy, 12) if energy is not None else None,
        "average_power_watts": (
            round(lhm_sampler.average_power_watts, 3)
            if lhm_sampler and lhm_sampler.average_power_watts is not None
            else None
        ),
        "energy_sample_count": len(lhm_sampler.samples) if lhm_sampler else 0,
        "energy_error": lhm_sampler.last_error if lhm_sampler and energy is None else None,
        "carbon_emissions_g": round(carbon_grams, 9) if carbon_grams is not None else None,
        "grid_carbon": carbon_reading,
        "quality_score": round(quality, 4),
        "request_count": len(results),
        "successful_requests": len(successful),
        "error_rate": round(1 - len(successful) / len(results), 6) if results else 1.0,
        "energy_source": "+".join(energy_sources) if energy_sources else "unavailable",
        "metric_evidence": {
            "performance": "measured",
            "energy": "measured" if energy is not None else "unavailable",
        },
        "process_cpu_seconds": process_cpu,
        "cgroup_cpu": cgroup_cpu,
    }


async def _report_transaction_events(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    agent_id: uuid.UUID,
    desired_state_version: int,
    transaction_result: Any,
) -> None:
    for event in transaction_result.events:
        action = str(event["action"])
        status = (
            "failed"
            if not transaction_result.passed
            else "rolled_back"
            if "rollback" in action
            else "completed"
        )
        response = await client.post(
            f"{base_url}/api/v1/agents/{agent_id}/events",
            headers=headers,
            json={
                "desiredStateVersion": desired_state_version,
                "control": event["control"],
                "action": action,
                "status": status,
                "result": event["result"],
            },
        )
        response.raise_for_status()


async def _run_assigned_benchmark(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    agent_id: uuid.UUID,
    assignment: dict[str, Any],
    desired_state_version: int,
    collector: SystemCollector,
    transaction: ControlTransaction,
    profile_changed: Callable[[str], None],
) -> None:
    configuration = assignment.get("configuration") or {}
    warmup = int(configuration.get("warmupSeconds", 60))
    phase = int(configuration.get("phaseSeconds", 180))
    cooldown = int(configuration.get("cooldownSeconds", 60))
    concurrency = int(configuration.get("concurrency", 2))
    prompt_ids = tuple(
        str(value)
        for value in configuration.get("promptIds", ["returns", "shipping", "exchange", "delay"])
    )
    benchmark_id = str(assignment["id"])
    initial_restore = transaction.restore()
    await _report_transaction_events(
        client,
        base_url,
        headers,
        agent_id,
        desired_state_version,
        initial_restore,
    )
    profile_changed("observe")
    try:
        await _benchmark_phase(
            client,
            base_url,
            headers,
            agent_id,
            benchmark_id,
            warmup,
            concurrency,
            collector,
            prompt_ids,
        )
        baseline = await _benchmark_phase(
            client,
            base_url,
            headers,
            agent_id,
            benchmark_id,
            phase,
            concurrency,
            collector,
            prompt_ids,
        )
        baseline_restore = transaction.restore()
        await _report_transaction_events(
            client,
            base_url,
            headers,
            agent_id,
            desired_state_version,
            baseline_restore,
        )
        await asyncio.sleep(cooldown)
        applied = transaction.apply({"profile": str(assignment["profile"])})
        await _report_transaction_events(
            client,
            base_url,
            headers,
            agent_id,
            desired_state_version,
            applied,
        )
        if not applied.passed:
            raise RuntimeError("Benchmark optimization profile failed to apply")
        profile_changed(str(assignment["profile"]))
        optimized = await _benchmark_phase(
            client,
            base_url,
            headers,
            agent_id,
            benchmark_id,
            phase,
            concurrency,
            collector,
            prompt_ids,
        )
    except Exception as exc:
        failure = await client.post(
            f"{base_url}/api/v1/agents/{agent_id}/events",
            headers=headers,
            json={
                "desiredStateVersion": desired_state_version,
                "control": "benchmark",
                "action": "failed",
                "status": "failed",
                "result": {
                    "benchmarkId": benchmark_id,
                    "error": type(exc).__name__,
                    "errorDetail": str(exc)[:500],
                    "evidence": "measured",
                },
            },
        )
        failure.raise_for_status()
        raise
    finally:
        rollback = transaction.restore()
        profile_changed("observe")
        await _report_transaction_events(
            client,
            base_url,
            headers,
            agent_id,
            desired_state_version,
            rollback,
        )
    result = await client.post(
        f"{base_url}/api/v1/agents/{agent_id}/events",
        headers=headers,
        json={
            "desiredStateVersion": desired_state_version,
            "control": "benchmark",
            "action": "complete",
            "status": "completed",
            "result": {
                "benchmarkId": benchmark_id,
                "baseline": baseline,
                "optimized": optimized,
                "evidence": "measured",
            },
        },
    )
    result.raise_for_status()


async def run() -> None:
    if platform.system().lower() != "linux":
        raise RuntimeError(
            "The real EcoRoute node agent is Linux-only; use the simulator elsewhere"
        )
    base_url = os.environ["ECOROUTE_PUBLIC_URL"].rstrip("/")
    token = os.environ["ECOROUTE_AGENT_TOKEN"]
    state_dir = Path(os.environ.get("ECOROUTE_AGENT_STATE_DIR", "/var/lib/ecoroute-agent"))
    agent_id = load_agent_id(state_dir / "agent-id")
    capabilities, detection_errors = detect_capabilities()
    collector = SystemCollector()
    headers = {"Authorization": f"Bearer {token}"}
    registration = {
        "agentId": str(agent_id),
        "hostname": socket.gethostname(),
        "agentVersion": "0.1.0",
        "platform": platform.system().lower(),
        "kernelVersion": platform.release(),
        "capabilities": capabilities,
    }
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(handled_signal, stop.set)

    active_profile = "observe"
    applied_version = 0
    sequence = 0
    samples: deque[dict[str, Any]] = deque(maxlen=60)
    pending_events: deque[dict[str, Any]] = deque(maxlen=100)
    last_heartbeat_success = loop.time()
    last_detection = loop.time()
    guardrail_failures = 0
    transaction: ControlTransaction | None = None
    benchmark_task: asyncio.Task[None] | None = None

    def set_active_profile(value: str) -> None:
        nonlocal active_profile
        active_profile = value

    async with httpx.AsyncClient(timeout=10) as client:
        while not stop.is_set():
            try:
                response = await client.post(
                    f"{base_url}/api/v1/agents/register", headers=headers, json=registration
                )
                response.raise_for_status()
                desired = response.json()
                server_approved = set(desired.get("approvedControls", []))
                approved = _local_approved() & server_approved
                controls = _build_controls(approved, capabilities)
                transaction = ControlTransaction(controls, state_dir / "rollback-snapshot.json")
                # Restore a crash-left snapshot before applying a fresh desired
                # state. The server can then reapply its current version cleanly.
                transaction.restore()
                last_heartbeat_success = loop.time()
                break
            except (httpx.HTTPError, OSError, ValueError, KeyError):
                await asyncio.sleep(2)

        next_heartbeat = 0.0
        next_telemetry = 0.0
        try:
            while not stop.is_set():
                now = loop.time()
                if benchmark_task is not None and benchmark_task.done():
                    try:
                        await benchmark_task
                        detection_errors.pop("benchmark", None)
                    except Exception as exc:
                        detection_errors["benchmark"] = f"Benchmark failed: {type(exc).__name__}"
                    benchmark_task = None
                if now - last_detection >= 600:
                    capabilities, detection_errors = detect_capabilities()
                    registration["capabilities"] = capabilities
                    last_detection = now

                if now >= next_heartbeat:
                    try:
                        response = await client.post(
                            f"{base_url}/api/v1/agents/{agent_id}/heartbeat",
                            headers=headers,
                            json={
                                "activeProfile": active_profile,
                                "lastAppliedStateVersion": applied_version,
                                "detectionErrors": detection_errors,
                            },
                        )
                        response.raise_for_status()
                        desired = response.json()
                        last_heartbeat_success = now
                        target_version = int(desired["desiredStateVersion"])
                        target_profile = str(desired["desiredProfile"])
                        if (
                            transaction is not None
                            and benchmark_task is None
                            and target_version > applied_version
                        ):
                            result = (
                                transaction.restore()
                                if target_profile in {"off", "observe"}
                                else transaction.apply({"profile": target_profile})
                            )
                            for event in result.events:
                                pending_events.append(
                                    {
                                        "desiredStateVersion": target_version,
                                        "control": event["control"],
                                        "action": event["action"],
                                        "status": "completed" if result.passed else "rolled_back",
                                        "result": event["result"],
                                    }
                                )
                            if result.passed:
                                active_profile = target_profile
                                applied_version = target_version
                        assignment = desired.get("benchmark")
                        if (
                            transaction is not None
                            and benchmark_task is None
                            and isinstance(assignment, dict)
                        ):
                            benchmark_task = asyncio.create_task(
                                _run_assigned_benchmark(
                                    client,
                                    base_url,
                                    headers,
                                    agent_id,
                                    assignment,
                                    target_version,
                                    collector,
                                    transaction,
                                    set_active_profile,
                                )
                            )
                        if (
                            benchmark_task is None
                            and active_profile in {"balanced", "eco"}
                            and _outside_guardrail(
                                active_profile, desired.get("guardrails", {})
                            )
                        ):
                            guardrail_failures += 1
                        else:
                            guardrail_failures = 0
                        if guardrail_failures >= 3 and transaction is not None:
                            rollback = transaction.restore()
                            active_profile = "observe"
                            for event in rollback.events:
                                pending_events.append(
                                    {
                                        "desiredStateVersion": applied_version,
                                        "control": event["control"],
                                        "action": "guardrail_rollback",
                                        "status": "rolled_back",
                                        "result": event["result"],
                                    }
                                )
                            guardrail_failures = 0
                        while pending_events:
                            event_response = await client.post(
                                f"{base_url}/api/v1/agents/{agent_id}/events",
                                headers=headers,
                                json=pending_events[0],
                            )
                            event_response.raise_for_status()
                            pending_events.popleft()
                    except httpx.HTTPError:
                        if (
                            now - last_heartbeat_success > 30
                            and transaction is not None
                            and active_profile not in {"off", "observe"}
                        ):
                            rollback = transaction.restore()
                            active_profile = "observe"
                            for event in rollback.events:
                                pending_events.append(
                                    {
                                        "desiredStateVersion": applied_version,
                                        "control": event["control"],
                                        "action": "heartbeat_loss_rollback",
                                        "status": "rolled_back",
                                        "result": event["result"],
                                    }
                                )
                    next_heartbeat = now + 5

                if now >= next_telemetry:
                    sequence += 1
                    hardware = collector.sample()
                    maximum_temperature = float(os.getenv("ECOROUTE_GPU_MAX_TEMPERATURE_C", "85"))
                    if (
                        any(
                            float(device.get("temperature_c", 0)) > maximum_temperature
                            for device in hardware.get("gpu", [])
                        )
                        and transaction is not None
                    ):
                        rollback = transaction.restore()
                        active_profile = "observe"
                        for event in rollback.events:
                            pending_events.append(
                                {
                                    "desiredStateVersion": applied_version,
                                    "control": event["control"],
                                    "action": "health_rollback",
                                    "status": "rolled_back",
                                    "result": event["result"],
                                }
                            )
                    samples.append(
                        {
                            "agentId": str(agent_id),
                            "sequence": sequence,
                            "observedAt": datetime.now(timezone.utc).isoformat(),
                            "profile": active_profile,
                            **hardware,
                            "evidence": "measured",
                        }
                    )
                    try:
                        telemetry = await client.post(
                            f"{base_url}/api/v1/agents/{agent_id}/telemetry",
                            headers=headers,
                            json=list(samples),
                        )
                        telemetry.raise_for_status()
                        samples.clear()
                    except httpx.HTTPError:
                        pass
                    next_telemetry = now + 1
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.2)
                except TimeoutError:
                    pass
        finally:
            if benchmark_task is not None and not benchmark_task.done():
                benchmark_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await benchmark_task
            if transaction is not None:
                transaction.restore()
            collector.close()


if __name__ == "__main__":
    asyncio.run(run())
