from __future__ import annotations

import asyncio
import os
import platform
import signal
import socket
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import httpx
from ecoroute.db.base import uuid7

from ecoroute_agent.controls import ControlTransaction, SimulatorProfileControl
from ecoroute_agent.simulator import SimulatorModel


def load_agent_id(path: Path) -> uuid.UUID:
    if path.exists():
        return uuid.UUID(path.read_text().strip())
    value = uuid7()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(str(value))
    path.chmod(0o600)
    return value


async def run() -> None:
    base_url = os.environ.get("ECOROUTE_PUBLIC_URL", "http://gateway:8000").rstrip("/")
    token = os.environ.get("ECOROUTE_AGENT_TOKEN", "replace-me")
    seed = int(os.environ.get("ECOROUTE_SIMULATOR_SEED", "42"))
    state_dir = Path(os.environ.get("ECOROUTE_AGENT_STATE_DIR", "/var/lib/ecoroute-agent"))
    agent_id = load_agent_id(state_dir / "agent-id")
    headers = {"Authorization": f"Bearer {token}"}
    registration = {
        "agentId": str(agent_id),
        "hostname": socket.gethostname(),
        "agentVersion": "0.1.0",
        "platform": platform.system().lower(),
        "kernelVersion": platform.release(),
        "capabilities": {
            "nvmlEnergy": True,
            "nvmlPowerLimit": True,
            "rapl": True,
            "cgroupsV2": True,
            "niceIonice": True,
            "schedExt": False,
            "napiNetdevGenl": True,
            "simulator": True,
        },
    }
    model = SimulatorModel(seed)
    control = SimulatorProfileControl()
    transaction = ControlTransaction([control], state_dir / "rollback-snapshot.json")
    transaction.restore()
    applied_version = 0
    sample_buffer: deque[dict[str, Any]] = deque(maxlen=60)
    event_buffer: deque[dict[str, Any]] = deque(maxlen=100)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(handled_signal, stop.set)

    async with httpx.AsyncClient(timeout=10) as client:
        while not stop.is_set():
            try:
                response = await client.post(
                    f"{base_url}/api/v1/agents/register", headers=headers, json=registration
                )
                response.raise_for_status()
                break
            except httpx.HTTPError:
                await asyncio.sleep(2)
        last_heartbeat_success = loop.time()
        next_heartbeat = 0.0
        try:
            while not stop.is_set():
                now = loop.time()
                if now >= next_heartbeat:
                    try:
                        response = await client.post(
                            f"{base_url}/api/v1/agents/{agent_id}/heartbeat",
                            headers=headers,
                            json={
                                "activeProfile": control.active,
                                "lastAppliedStateVersion": applied_version,
                            },
                        )
                        response.raise_for_status()
                        desired = response.json()
                        last_heartbeat_success = now
                        target_version = int(desired["desiredStateVersion"])
                        if target_version > applied_version:
                            target_profile = str(desired["desiredProfile"])
                            result = (
                                transaction.restore()
                                if target_profile in {"off", "observe"}
                                else transaction.apply({"profile": target_profile})
                            )
                            if target_profile in {"off", "observe"} and result.passed:
                                control.active = target_profile
                            for event in result.events:
                                event_buffer.append(
                                    {
                                        "desiredStateVersion": target_version,
                                        "control": event["control"],
                                        "action": event["action"],
                                        "status": "completed" if result.passed else "rolled_back",
                                        "result": event["result"],
                                    }
                                )
                            if result.passed:
                                applied_version = target_version
                        while event_buffer:
                            sent = await client.post(
                                f"{base_url}/api/v1/agents/{agent_id}/events",
                                headers=headers,
                                json=event_buffer[0],
                            )
                            sent.raise_for_status()
                            event_buffer.popleft()
                    except httpx.HTTPError:
                        if now - last_heartbeat_success > 30 and control.active not in {
                            "off",
                            "observe",
                        }:
                            rollback = transaction.restore()
                            control.active = "observe"
                            for event in rollback.events:
                                event_buffer.append(
                                    {
                                        "desiredStateVersion": applied_version,
                                        "control": event["control"],
                                        "action": "heartbeat_loss_rollback",
                                        "status": "rolled_back",
                                        "result": event["result"],
                                    }
                                )
                    next_heartbeat = now + 5
                sample_buffer.append(model.sample(str(agent_id), control.active))
                try:
                    sent = await client.post(
                        f"{base_url}/api/v1/agents/{agent_id}/telemetry",
                        headers=headers,
                        json=list(sample_buffer),
                    )
                    sent.raise_for_status()
                    sample_buffer.clear()
                except httpx.HTTPError:
                    pass
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass
        finally:
            transaction.restore()


if __name__ == "__main__":
    asyncio.run(run())
