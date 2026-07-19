from pathlib import Path

import pytest
from ecoroute_agent.collectors.libre_hardware_monitor import (
    LibreHardwareMonitorPowerSampler,
    cpu_package_power_watts,
)
from ecoroute_agent.collectors.system import RaplCollector, _RaplCounter
from ecoroute_agent.controls import host
from ecoroute_agent.controls.host import (
    CgroupV2Control,
    GatewayConcurrencyControl,
    NiceIoniceControl,
)
from ecoroute_agent.controls.transaction import ControlTransaction, SimulatorProfileControl
from ecoroute_agent.real_main import _energy_counter_kwh
from ecoroute_agent.simulator import SimulatorModel


def test_extracts_cpu_package_power_from_lhm_tree() -> None:
    payload = {
        "Children": [
            {
                "Text": "Powers",
                "Children": [
                    {
                        "Text": "CPU Package",
                        "Value": "36.7 W",
                        "SensorId": "/intelcpu/0/power/0",
                    }
                ],
            }
        ]
    }
    assert cpu_package_power_watts(payload) == 36.7


def test_integrates_lhm_power_samples_to_kwh() -> None:
    sampler = LibreHardwareMonitorPowerSampler("http://localhost/data.json")
    sampler.samples = [(10.0, 36.0), (20.0, 36.0)]
    assert sampler.energy_kwh == pytest.approx(0.0001)
    assert sampler.average_power_watts == pytest.approx(36.0)


def test_simulator_never_claims_measured() -> None:
    model = SimulatorModel(42)
    sample = model.sample("00000000-0000-0000-0000-000000000001", "eco")
    assert sample["evidence"] == "simulated"
    assert sample["gpu"][0]["uuid"].startswith("GPU-SIMULATED")


def test_transaction_applies_and_snapshots(tmp_path: Path) -> None:
    control = SimulatorProfileControl()
    transaction = ControlTransaction([control], tmp_path / "snapshot.json")
    result = transaction.apply({"profile": "balanced"})
    assert result.passed
    assert control.active == "balanced"
    assert (tmp_path / "snapshot.json").stat().st_mode & 0o777 == 0o600


class FailingControl(SimulatorProfileControl):
    name = "failing"

    def verify(self, plan: str):
        return {"passed": False}


class PartiallyApplyingControl(SimulatorProfileControl):
    name = "partial"

    def apply(self, plan: str):
        self.active = plan
        raise RuntimeError("partial apply failure")


def test_verification_failure_rolls_back_prior_controls(tmp_path: Path) -> None:
    first = SimulatorProfileControl()
    failure = FailingControl()
    transaction = ControlTransaction([first, failure], tmp_path / "snapshot.json")
    result = transaction.apply({"profile": "eco"})
    assert not result.passed
    assert first.active == "observe"


def test_partial_apply_failure_rolls_back_current_control(tmp_path: Path) -> None:
    control = PartiallyApplyingControl()
    transaction = ControlTransaction([control], tmp_path / "snapshot.json")
    result = transaction.apply({"profile": "eco"})
    assert not result.passed
    assert control.active == "observe"


def test_restore_replays_original_snapshot_and_removes_it(tmp_path: Path) -> None:
    control = SimulatorProfileControl()
    snapshot = tmp_path / "snapshot.json"
    transaction = ControlTransaction([control], snapshot)
    assert transaction.apply({"profile": "eco"}).passed
    assert control.active == "eco"
    restored = transaction.restore()
    assert restored.passed
    assert control.active == "observe"
    assert not snapshot.exists()


def test_gateway_concurrency_clamps_and_rolls_back() -> None:
    control = GatewayConcurrencyControl(3)
    original = control.snapshot()
    assert control.plan({"profile": "balanced"}) == 2
    assert control.plan({"profile": "eco"}) == 2
    control.apply(1)
    assert control.verify(1)["passed"]
    control.rollback(original)
    assert control.target == 3


def test_rapl_counter_handles_energy_wrap(tmp_path: Path) -> None:
    energy = tmp_path / "energy_uj"
    energy.write_text("5")
    collector = RaplCollector.__new__(RaplCollector)
    collector._counters = [
        _RaplCounter(energy_path=energy, maximum=100, previous=90, cumulative=90)
    ]
    assert collector.sample() == 105


def test_rapl_microjoules_convert_to_kwh() -> None:
    assert _energy_counter_kwh({"rapl_energy_uj": 3_600_000_000, "gpu": []}) == 1


def test_cgroup_control_applies_real_v2_values_in_order(tmp_path: Path) -> None:
    root = tmp_path / "ecoroute.slice"
    root.mkdir()
    (root / "cgroup.subtree_control").write_text("")
    control = CgroupV2Control(root, [], [], allow_hard_quota=True)
    plan = control.plan({"profile": "eco"})

    result = control.apply(plan)

    assert result["changed"] is True
    assert (root / "cgroup.subtree_control").read_text() == "+cpu"
    assert (root / "inference" / "cpu.weight").read_text() == "900"
    assert (root / "background" / "cpu.weight").read_text() == "25"
    assert (root / "background" / "cpu.max").read_text() == "20000 100000"
    assert control.verify(plan)["passed"] is True


def test_nice_control_rejects_pid_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return 20.0

        def nice(self, value=None):
            del value
            return 0

        def ionice(self, *args, **kwargs):
            del args, kwargs
            return (2, 0)

    monkeypatch.setattr(host.psutil, "Process", Process)
    control = NiceIoniceControl.__new__(NiceIoniceControl)
    control.pids = {123: 10.0}
    with pytest.raises(RuntimeError, match="PID 123 was reused"):
        control.apply(control.plan({"profile": "eco"}))
