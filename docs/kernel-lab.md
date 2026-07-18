# Real Linux kernel-control demo

The kernel lab runs EcoRoute in a four-vCPU, 8 GiB ARM64 Ubuntu VM and replaces the node
simulator with the real Linux agent. It creates two native workloads in the guest:

- an OpenAI-compatible CPU-bound inference target;
- four continuously busy background worker processes.

During the baseline phase both workloads compete normally. During the optimized phase the real
agent creates cgroup v2 `inference` and `background` groups, sets the inference CPU weight to 900,
sets the background weight to 25, and caps the entire background group at 20% of one CPU. The
agent verifies both the values and PID placement, records the kernel's `cpu.stat` counters, and
rolls every process back to its original systemd cgroup after the benchmark.

## Requirements

- Apple-silicon Mac with macOS 13 or newer.
- Homebrew.
- At least 15 GB of free disk. The Lima disk is sparse but the Ubuntu and EcoRoute Docker images
  still consume several gigabytes.
- Internet access for the first VM and image build.

## Run

```bash
./scripts/kernel-lab-up
./scripts/kernel-lab-demo
```

Open the control center at <http://localhost:3000> and select **Self-Hosted Nodes**. The node must
show `measured` evidence, `cgroups_v2` and `nice_ionice` as detected and approved, and the
`kernel-lab-cpu` endpoint as its benchmark target.

The command-line benchmark takes about 70 seconds. It prints baseline and optimized throughput,
p50/p95 latency, per-process CPU seconds, cgroup throttling counters, and their comparison. The UI
shows the same comparison plus the real apply, verify, and rollback timeline.

Check or stop the lab with:

```bash
./scripts/kernel-lab-status
./scripts/kernel-lab-down
```

The background workload intentionally keeps the VM busy. Stop the lab when the demonstration is
over.

## Evidence boundary

Latency, throughput, process CPU time, cgroup settings, PID placement, and throttling are measured
from the guest. They are genuine Linux kernel behavior.

An Apple-silicon VM does not expose Intel RAPL or an NVIDIA total-energy counter. The benchmark
therefore reports energy as `unavailable`; it never substitutes a simulated energy value. A
supported bare-metal Intel or NVIDIA Linux host can run the same real agent to populate the energy
metrics.

The included target performs deterministic CPU work behind an OpenAI-compatible API so the lab is
credential-free and repeatable. To benchmark a trained model, replace the endpoint base URL with
that model server and set `ECOROUTE_INFERENCE_PIDS` to its worker PIDs; the kernel-control and
benchmark protocol remains unchanged.

## Safe demo claim

Report the observed result narrowly, for example:

> On a four-vCPU Ubuntu ARM64 VM under controlled CPU contention, EcoRoute's real cgroup profile
> changed p95 latency by X%, throughput by Y%, and background CPU time by Z%. The agent verified
> and rolled back every kernel control. Hardware energy was unavailable in this VM.

Do not describe VM performance results as measured physical-energy savings.
