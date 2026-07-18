from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path


def detect_capabilities() -> tuple[dict[str, bool], dict[str, str]]:
    errors: dict[str, str] = {}
    is_linux = platform.system().lower() == "linux"
    cgroup = Path("/sys/fs/cgroup/cgroup.controllers")
    rapl_paths = list(Path("/sys/class/powercap").glob("intel-rapl*/energy_uj")) if is_linux else []
    nvml = False
    nvml_energy = False
    nvml_limit = False
    if is_linux:
        try:
            import pynvml  # type: ignore[import-untyped]

            pynvml.nvmlInit()
            nvml = pynvml.nvmlDeviceGetCount() > 0
            if nvml:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                try:
                    pynvml.nvmlDeviceGetTotalEnergyConsumption(handle)
                    nvml_energy = True
                except pynvml.NVMLError:
                    errors["nvml_energy"] = "The GPU does not expose an energy counter."
                try:
                    pynvml.nvmlDeviceGetPowerManagementLimitConstraints(handle)
                    nvml_limit = True
                except pynvml.NVMLError:
                    errors["nvml_power_limit"] = (
                        "Power-limit management is not supported or permitted."
                    )
            pynvml.nvmlShutdown()
        except (ImportError, OSError, RuntimeError) as exc:
            errors["nvml"] = f"NVML unavailable: {type(exc).__name__}"
    capabilities = {
        "nvml_energy": nvml_energy,
        "nvml_power_limit": nvml_limit,
        "rapl": bool(rapl_paths),
        "cgroups_v2": is_linux and cgroup.exists() and os.access(cgroup.parent, os.R_OK | os.W_OK),
        "nice_ionice": is_linux and shutil.which("ionice") is not None,
        "sched_ext": is_linux and Path("/sys/kernel/sched_ext/state").exists(),
        "napi_netdev_genl": is_linux and shutil.which("ynl") is not None,
        "simulator": False,
    }
    return capabilities, errors
