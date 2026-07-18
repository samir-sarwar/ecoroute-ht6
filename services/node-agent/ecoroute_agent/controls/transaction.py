from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class HostControl(Protocol):
    name: str

    def snapshot(self) -> Any: ...
    def plan(self, desired: dict[str, Any]) -> Any: ...
    def apply(self, plan: Any) -> dict[str, Any]: ...
    def verify(self, plan: Any) -> dict[str, Any]: ...
    def rollback(self, snapshot: Any) -> dict[str, Any]: ...


@dataclass
class TransactionResult:
    passed: bool
    events: list[dict[str, Any]] = field(default_factory=list)


class ControlTransaction:
    def __init__(self, controls: list[HostControl], snapshot_path: Path) -> None:
        self.controls = controls
        self.snapshot_path = snapshot_path

    def apply(self, desired: dict[str, Any]) -> TransactionResult:
        snapshots = {control.name: control.snapshot() for control in self.controls}
        self.snapshot_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.snapshot_path.exists():
            self.snapshot_path.write_text(json.dumps(snapshots, separators=(",", ":")))
            os.chmod(self.snapshot_path, 0o600)
        applied: list[HostControl] = []
        events: list[dict[str, Any]] = []
        try:
            for control in self.controls:
                plan = control.plan(desired)
                # A control may partially mutate and then raise. Track it before
                # apply so that the failure path rolls it back as well.
                applied.append(control)
                result = control.apply(plan)
                events.append({"control": control.name, "action": "apply", "result": result})
                verification = control.verify(plan)
                events.append({"control": control.name, "action": "verify", "result": verification})
                if not verification.get("passed", False):
                    raise RuntimeError(f"verification failed for {control.name}")
            return TransactionResult(True, events)
        except Exception as exc:
            for control in reversed(applied):
                result = control.rollback(snapshots[control.name])
                events.append({"control": control.name, "action": "rollback", "result": result})
            events.append(
                {"control": "transaction", "action": "failed", "result": {"error": str(exc)}}
            )
            return TransactionResult(False, events)

    def restore(self) -> TransactionResult:
        if not self.snapshot_path.exists():
            return TransactionResult(True, [])
        snapshots = json.loads(self.snapshot_path.read_text())
        events: list[dict[str, Any]] = []
        passed = True
        current_names = {control.name for control in self.controls}
        missing_controls = set(snapshots) - current_names
        if missing_controls:
            passed = False
            events.append(
                {
                    "control": "transaction",
                    "action": "rollback",
                    "result": {
                        "passed": False,
                        "error": "snapshot contains unavailable controls",
                        "controls": sorted(missing_controls),
                    },
                }
            )
        for control in reversed(self.controls):
            try:
                result = control.rollback(snapshots[control.name])
                events.append({"control": control.name, "action": "rollback", "result": result})
            except Exception as exc:
                passed = False
                events.append(
                    {
                        "control": control.name,
                        "action": "rollback",
                        "result": {"passed": False, "error": str(exc)},
                    }
                )
        if passed:
            self.snapshot_path.unlink(missing_ok=True)
        return TransactionResult(passed, events)


class SimulatorProfileControl:
    name = "simulator_profile"

    def __init__(self) -> None:
        self.active = "observe"

    def snapshot(self) -> str:
        return self.active

    def plan(self, desired: dict[str, Any]) -> str:
        profile = str(desired["profile"])
        if profile not in {"off", "observe", "balanced", "eco"}:
            raise ValueError("invalid profile")
        return profile

    def apply(self, plan: str) -> dict[str, Any]:
        self.active = plan
        return {"profile": plan, "evidence": "simulated"}

    def verify(self, plan: str) -> dict[str, Any]:
        return {"passed": self.active == plan, "evidence": "simulated"}

    def rollback(self, snapshot: str) -> dict[str, Any]:
        self.active = snapshot
        return {"restored": snapshot, "evidence": "simulated"}
