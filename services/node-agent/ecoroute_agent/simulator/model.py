from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Any


class SimulatorModel:
    def __init__(self, seed: int) -> None:
        self.random = random.Random(seed)
        self.sequence = 0
        self.total_energy_mj = 80_000_000

    def sample(self, agent_id: str, profile: str) -> dict[str, Any]:
        self.sequence += 1
        phase = self.sequence / 8
        load = max(0.1, min(1.0, 0.58 + 0.28 * math.sin(phase)))
        multiplier = {"off": 1.0, "observe": 1.0, "balanced": 0.90, "eco": 0.78}[profile]
        power = (72 + 155 * load) * multiplier
        self.total_energy_mj += int(power * 1_000)
        return {
            "agentId": agent_id,
            "sequence": self.sequence,
            "observedAt": datetime.now(timezone.utc).isoformat(),
            "profile": profile,
            "cpuPercent": round(18 + 52 * load, 2),
            "memoryPercent": round(54 + 8 * load, 2),
            "networkRxBytes": int(18_000 + 8_000 * load),
            "networkTxBytes": int(7_000 + 4_000 * load),
            "gpu": [
                {
                    "uuid": "GPU-SIMULATED-0",
                    "utilization_percent": round(100 * load),
                    "memory_utilization_percent": round(45 + 20 * load),
                    "power_watts": round(power, 2),
                    "total_energy_mj": self.total_energy_mj,
                    "temperature_c": round(48 + 20 * load * multiplier, 1),
                    "power_limit_watts": round(240 * multiplier, 1),
                }
            ],
            "raplEnergyUj": int(self.total_energy_mj * 640),
            "evidence": "simulated",
        }
