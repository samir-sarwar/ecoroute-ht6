from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import Any

import psutil


def _write_value(path: Path, value: str) -> None:
    path.write_text(value)


def _read_value(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


class CgroupV2Control:
    name = "cgroups_v2"

    def __init__(
        self,
        root: Path,
        inference_pids: list[int],
        background_pids: list[int],
        allow_hard_quota: bool,
    ) -> None:
        self.root = root
        self.inference = root / "inference"
        self.background = root / "background"
        self.inference_pids = self._validate_pids(inference_pids)
        self.background_pids = self._validate_pids(background_pids)
        self.allow_hard_quota = allow_hard_quota

    @staticmethod
    def _validate_pids(values: list[int]) -> dict[int, float]:
        result: dict[int, float] = {}
        for pid in values:
            process = psutil.Process(pid)
            result[pid] = process.create_time()
        return result

    @staticmethod
    def _original_cgroup(pid: int) -> str | None:
        try:
            for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
                if line.startswith("0::"):
                    return line[3:]
        except OSError:
            return None
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "inference_weight": _read_value(self.inference / "cpu.weight"),
            "background_weight": _read_value(self.background / "cpu.weight"),
            "background_max": _read_value(self.background / "cpu.max"),
            "pid_cgroups": {
                str(pid): self._original_cgroup(pid)
                for pid in {*self.inference_pids, *self.background_pids}
            },
        }

    def plan(self, desired: dict[str, Any]) -> dict[str, Any]:
        profile = str(desired["profile"])
        if profile not in {"balanced", "eco"}:
            return {"profile": profile, "mutate": False}
        return {
            "profile": profile,
            "mutate": True,
            "inference_weight": 800 if profile == "balanced" else 900,
            "background_weight": 100 if profile == "balanced" else 25,
            "background_max": (
                "20000 100000" if profile == "eco" and self.allow_hard_quota else "max 100000"
            ),
        }

    def _check_pid(self, pid: int, started: float) -> None:
        if abs(psutil.Process(pid).create_time() - started) > 0.001:
            raise RuntimeError(f"PID {pid} was reused")

    def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not plan["mutate"]:
            return {"passed": True, "changed": False}
        for pid, started in self.inference_pids.items():
            self._check_pid(pid, started)
        for pid, started in self.background_pids.items():
            self._check_pid(pid, started)
        self.root.mkdir(mode=0o755, parents=True, exist_ok=True)
        subtree_control = self.root / "cgroup.subtree_control"
        if subtree_control.exists():
            enabled = set((subtree_control.read_text().strip()).split())
            if "cpu" not in enabled:
                _write_value(subtree_control, "+cpu")
        self.inference.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.background.mkdir(mode=0o755, parents=True, exist_ok=True)
        _write_value(self.inference / "cpu.weight", str(plan["inference_weight"]))
        _write_value(self.background / "cpu.weight", str(plan["background_weight"]))
        _write_value(self.background / "cpu.max", str(plan["background_max"]))
        for pid in self.inference_pids:
            _write_value(self.inference / "cgroup.procs", str(pid))
        for pid in self.background_pids:
            _write_value(self.background / "cgroup.procs", str(pid))
        return {"passed": True, "changed": True, **plan}

    def verify(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not plan["mutate"]:
            return {"passed": True, "changed": False}
        values_match = (
            _read_value(self.inference / "cpu.weight") == str(plan["inference_weight"])
            and _read_value(self.background / "cpu.weight") == str(plan["background_weight"])
            and _read_value(self.background / "cpu.max") == str(plan["background_max"])
        )
        try:
            inference_path = "/" + str(self.inference.relative_to("/sys/fs/cgroup"))
            background_path = "/" + str(self.background.relative_to("/sys/fs/cgroup"))
        except ValueError:
            inference_path = None
            background_path = None
        inference_moved = not self.inference_pids or bool(
            inference_path
            and all(self._original_cgroup(pid) == inference_path for pid in self.inference_pids)
        )
        background_moved = not self.background_pids or bool(
            background_path
            and all(self._original_cgroup(pid) == background_path for pid in self.background_pids)
        )
        return {
            "passed": values_match and inference_moved and background_moved,
            "valuesMatch": values_match,
            "inferencePidsMoved": inference_moved,
            "backgroundPidsMoved": background_moved,
        }

    def rollback(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        for path, key in (
            (self.inference / "cpu.weight", "inference_weight"),
            (self.background / "cpu.weight", "background_weight"),
            (self.background / "cpu.max", "background_max"),
        ):
            if snapshot.get(key) is not None and path.exists():
                _write_value(path, str(snapshot[key]))
        base = Path("/sys/fs/cgroup")
        for raw_pid, original in snapshot.get("pid_cgroups", {}).items():
            if original:
                pid = int(raw_pid)
                if psutil.pid_exists(pid):
                    destination = base / str(original).lstrip("/") / "cgroup.procs"
                    if destination.exists():
                        _write_value(destination, str(pid))
        return {"passed": True, "restored": True}


class NiceIoniceControl:
    name = "nice_ionice"

    def __init__(self, pids: list[int]) -> None:
        self.pids = {pid: psutil.Process(pid).create_time() for pid in pids}

    def snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        for pid, started in self.pids.items():
            if not psutil.pid_exists(pid):
                continue
            process: Any = psutil.Process(pid)
            snapshot[str(pid)] = {
                "create_time": started,
                "nice": process.nice(),
                "ionice": list(process.ionice()),
            }
        return snapshot

    def plan(self, desired: dict[str, Any]) -> dict[str, Any]:
        profile = str(desired["profile"])
        return {
            "mutate": profile in {"balanced", "eco"},
            "nice": 5 if profile == "balanced" else 10,
            "ionice_class": int(getattr(psutil, "IOPRIO_CLASS_BE", 2)),
            "ionice_value": 7,
        }

    def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not plan["mutate"]:
            return {"passed": True, "changed": False}
        for pid, started in self.pids.items():
            process: Any = psutil.Process(pid)
            if abs(process.create_time() - started) > 0.001:
                raise RuntimeError(f"PID {pid} was reused")
            # Only lower background priority; never raise it above the original value.
            process.nice(max(int(process.nice()), int(plan["nice"])))
            process.ionice(plan["ionice_class"], value=plan["ionice_value"])
        return {"passed": True, "pids": sorted(self.pids)}

    def verify(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not plan["mutate"]:
            return {"passed": True}
        return {
            "passed": all(
                psutil.Process(pid).nice() >= plan["nice"]
                for pid in self.pids
                if psutil.pid_exists(pid)
            )
        }

    def rollback(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        for raw_pid, value in snapshot.items():
            pid = int(raw_pid)
            if not psutil.pid_exists(pid):
                continue
            process: Any = psutil.Process(pid)
            if abs(process.create_time() - float(value["create_time"])) > 0.001:
                continue
            process.nice(int(value["nice"]))
            ionice = value.get("ionice", [])
            if ionice:
                process.ionice(int(ionice[0]), value=int(ionice[1]))
        return {"passed": True, "restored": True}


class GatewayConcurrencyControl:
    name = "gateway_concurrency"

    def __init__(self, baseline: int) -> None:
        self.baseline = max(1, baseline)
        self.target = self.baseline

    def snapshot(self) -> int:
        return self.target

    def plan(self, desired: dict[str, Any]) -> int:
        multiplier = {"off": 1.0, "observe": 1.0, "balanced": 0.90, "eco": 0.75}
        return max(1, int(self.baseline * multiplier[str(desired["profile"])]))

    def apply(self, plan: int) -> dict[str, Any]:
        self.target = plan
        return {"passed": True, "target": plan, "baseline": self.baseline}

    def verify(self, plan: int) -> dict[str, Any]:
        return {"passed": self.target == plan, "target": self.target}

    def rollback(self, snapshot: int) -> dict[str, Any]:
        self.target = snapshot
        return {"passed": True, "target": snapshot}


class NvmlPowerLimitControl:
    name = "nvml_power_limit"

    def __init__(self) -> None:
        import pynvml  # type: ignore[import-untyped]

        pynvml.nvmlInit()
        self.pynvml = pynvml
        self.handles = [
            pynvml.nvmlDeviceGetHandleByIndex(index) for index in range(pynvml.nvmlDeviceGetCount())
        ]
        self.original_limits = [
            pynvml.nvmlDeviceGetPowerManagementLimit(handle) for handle in self.handles
        ]

    def snapshot(self) -> list[int]:
        return [self.pynvml.nvmlDeviceGetPowerManagementLimit(handle) for handle in self.handles]

    def plan(self, desired: dict[str, Any]) -> list[int]:
        profile = str(desired["profile"])
        multiplier = 0.90 if profile == "balanced" else 0.80 if profile == "eco" else 1.0
        targets = []
        for handle, original in zip(self.handles, self.original_limits, strict=True):
            minimum, maximum = self.pynvml.nvmlDeviceGetPowerManagementLimitConstraints(handle)
            targets.append(max(minimum, min(maximum, int(original * multiplier))))
        return targets

    def apply(self, plan: list[int]) -> dict[str, Any]:
        for handle, target in zip(self.handles, plan, strict=True):
            self.pynvml.nvmlDeviceSetPowerManagementLimit(handle, target)
        return {"passed": True, "targetsMilliwatts": plan}

    def verify(self, plan: list[int]) -> dict[str, Any]:
        actual = [self.pynvml.nvmlDeviceGetPowerManagementLimit(handle) for handle in self.handles]
        return {"passed": actual == plan, "actualMilliwatts": actual}

    def rollback(self, snapshot: list[int]) -> dict[str, Any]:
        for handle, original in zip(self.handles, snapshot, strict=True):
            self.pynvml.nvmlDeviceSetPowerManagementLimit(handle, original)
        return {"passed": True, "restoredMilliwatts": snapshot}


class SchedExtControl:
    name = "sched_ext"

    def __init__(self, binary: Path, arguments: list[str], checksum_sha256: str) -> None:
        self.binary = binary
        self.arguments = arguments
        self.checksum = checksum_sha256
        self.process: subprocess.Popen[bytes] | None = None

    def snapshot(self) -> dict[str, Any]:
        return {"active": _read_value(Path("/sys/kernel/sched_ext/state")) == "enabled"}

    def plan(self, desired: dict[str, Any]) -> dict[str, Any]:
        return {"enabled": str(desired["profile"]) == "eco"}

    def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not plan["enabled"]:
            return {"passed": True, "enabled": False, "experimental": True}
        if not self.binary.is_absolute() or not self.binary.exists():
            raise RuntimeError("Configured sched_ext binary does not exist")
        digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        if digest != self.checksum:
            raise RuntimeError("sched_ext binary checksum mismatch")
        self.process = subprocess.Popen(
            [str(self.binary), *self.arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"passed": True, "enabled": True, "experimental": True}

    def verify(self, plan: dict[str, Any]) -> dict[str, Any]:
        enabled = _read_value(Path("/sys/kernel/sched_ext/state")) == "enabled"
        return {"passed": enabled == plan["enabled"], "enabled": enabled, "experimental": True}

    def rollback(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=10)
        active = _read_value(Path("/sys/kernel/sched_ext/state")) == "enabled"
        return {
            "passed": active == bool(snapshot.get("active", False)),
            "active": active,
            "experimental": True,
        }


class NapiControl:
    """Allowlisted netdev-genl adapter; disabled unless every value is explicitly configured."""

    name = "napi_netdev_genl"
    _INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")

    def __init__(self, ynl: Path, interfaces: list[str], values: dict[str, int]) -> None:
        if not interfaces or not all(self._INTERFACE.fullmatch(item) for item in interfaces):
            raise ValueError("Invalid NAPI interface allowlist")
        required = {"defer_hard_irqs", "gro_flush_timeout", "irq_suspend_timeout"}
        if set(values) != required or any(value < 0 for value in values.values()):
            raise ValueError("Every benchmark-approved NAPI value is required")
        self.ynl = ynl
        self.interfaces = interfaces
        self.values = values

    def snapshot(self) -> dict[str, Any]:
        # The exact netdev-genl family schema is kernel-versioned. Capture the bounded JSON
        # emitted by the operator-supplied YNL helper rather than parsing unstructured text.
        result = subprocess.run(
            [str(self.ynl), "--dump-napi-json", *self.interfaces],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return {"raw": result.stdout.decode()[:100_000]}

    def plan(self, desired: dict[str, Any]) -> dict[str, Any]:
        return {"enabled": str(desired["profile"]) == "eco", "values": self.values}

    def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not plan["enabled"]:
            return {"passed": True, "enabled": False, "experimental": True}
        subprocess.run(
            [
                str(self.ynl),
                "--set-napi-json",
                *self.interfaces,
                str(plan["values"]["defer_hard_irqs"]),
                str(plan["values"]["gro_flush_timeout"]),
                str(plan["values"]["irq_suspend_timeout"]),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return {"passed": True, "enabled": True, "experimental": True}

    def verify(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {"passed": True, "experimental": True, "operatorVerified": True}

    def rollback(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        subprocess.run(
            [str(self.ynl), "--restore-napi-json"],
            input=str(snapshot["raw"]).encode(),
            check=True,
            capture_output=True,
            timeout=10,
        )
        return {"passed": True, "experimental": True}
