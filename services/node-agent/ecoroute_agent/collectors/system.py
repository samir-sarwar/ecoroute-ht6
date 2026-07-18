from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil


@dataclass
class _RaplCounter:
    energy_path: Path
    maximum: int
    previous: int
    cumulative: int


class RaplCollector:
    def __init__(self) -> None:
        self._counters: list[_RaplCounter] = []
        for path in Path("/sys/class/powercap").glob("intel-rapl*/energy_uj"):
            try:
                current = int(path.read_text().strip())
                maximum_path = path.with_name("max_energy_range_uj")
                maximum = int(maximum_path.read_text().strip()) if maximum_path.exists() else 0
                self._counters.append(_RaplCounter(path, maximum, current, current))
            except (OSError, ValueError):
                continue

    def sample(self) -> int | None:
        total = 0
        valid = False
        for counter in self._counters:
            try:
                current = int(counter.energy_path.read_text().strip())
            except (OSError, ValueError):
                continue
            delta = current - counter.previous
            if delta < 0 and counter.maximum > 0:
                delta = counter.maximum - counter.previous + current
            if delta >= 0:
                counter.cumulative += delta
            counter.previous = current
            total += counter.cumulative
            valid = True
        return total if valid else None


class NvmlCollector:
    def __init__(self) -> None:
        self._pynvml: Any | None = None
        self._handles: list[Any] = []
        try:
            import pynvml  # type: ignore[import-untyped]

            pynvml.nvmlInit()
            self._pynvml = pynvml
            self._handles = [
                pynvml.nvmlDeviceGetHandleByIndex(index)
                for index in range(pynvml.nvmlDeviceGetCount())
            ]
        except (ImportError, OSError, RuntimeError):
            self._pynvml = None
            self._handles = []

    def sample(self) -> list[dict[str, Any]]:
        if self._pynvml is None:
            return []
        pynvml = self._pynvml
        values: list[dict[str, Any]] = []
        for handle in self._handles:
            try:
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                raw_uuid = pynvml.nvmlDeviceGetUUID(handle)
                device_uuid = raw_uuid.decode() if isinstance(raw_uuid, bytes) else str(raw_uuid)
                try:
                    total_energy = int(pynvml.nvmlDeviceGetTotalEnergyConsumption(handle))
                except pynvml.NVMLError:
                    total_energy = None
                values.append(
                    {
                        "uuid": device_uuid,
                        "utilization_percent": float(utilization.gpu),
                        "memory_utilization_percent": float(utilization.memory),
                        "power_watts": pynvml.nvmlDeviceGetPowerUsage(handle) / 1000,
                        "total_energy_mj": total_energy,
                        "temperature_c": float(
                            pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                        ),
                        "power_limit_watts": pynvml.nvmlDeviceGetPowerManagementLimit(handle)
                        / 1000,
                    }
                )
            except pynvml.NVMLError:
                continue
        return values

    def close(self) -> None:
        if self._pynvml is not None:
            try:
                self._pynvml.nvmlShutdown()
            except self._pynvml.NVMLError:
                pass


class SystemCollector:
    def __init__(self) -> None:
        counters = psutil.net_io_counters()
        self._last_network = (counters.bytes_recv, counters.bytes_sent)
        self._rapl = RaplCollector()
        self._nvml = NvmlCollector()

    def sample(self) -> dict[str, Any]:
        counters = psutil.net_io_counters()
        rx, tx = counters.bytes_recv, counters.bytes_sent
        payload: dict[str, Any] = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": psutil.virtual_memory().percent,
            "network_rx_bytes": max(0, rx - self._last_network[0]),
            "network_tx_bytes": max(0, tx - self._last_network[1]),
            "gpu": self._nvml.sample(),
            "rapl_energy_uj": self._rapl.sample(),
        }
        self._last_network = (rx, tx)
        return payload

    def close(self) -> None:
        self._nvml.close()
