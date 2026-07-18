# Windows 11 real kernel-control demo

Use WSL2 Ubuntu rather than trying to apply Linux cgroups to the Windows kernel. WSL2 runs a real
Linux kernel, so the cgroup v2 controls, CPU accounting, process placement, throttling, and rollback
in this demo are genuine. Docker Engine runs inside the same Ubuntu environment as the native
workloads and agent.

## 1. Install and size WSL2

Run PowerShell as Administrator:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart Windows if requested, open Ubuntu once, and create the Linux username and password. Then
check that the distribution is WSL2:

```powershell
wsl --update
wsl --list --verbose
```

For a Windows machine with at least 16 GB of RAM, create or merge these settings into
`$env:USERPROFILE\.wslconfig`:

```ini
[wsl2]
processors=4
memory=8GB
swap=4GB
localhostForwarding=true
```

If the Windows computer has only 8 GB total RAM, use `memory=5GB` instead. Apply the settings:

```powershell
wsl --shutdown
```

Current Ubuntu installations made by `wsl --install` use systemd by default. The lab checks this
and prints the exact recovery steps if systemd is disabled.

## 2. Put the repository in Ubuntu

The lab can run from a Windows path such as `/mnt/c/Users/...`, but Docker builds are faster on
WSL's Linux filesystem. From Ubuntu, copy a repository already present on Windows, excluding local
dependency caches:

```bash
sudo apt-get update && sudo apt-get install -y rsync
mkdir -p ~/src/ht6
rsync -a --exclude node_modules --exclude .venv --exclude .next \
  /mnt/c/Users/YOUR_WINDOWS_USER/path/to/ht6/ ~/src/ht6/
cd ~/src/ht6
```

Alternatively, clone the repository directly into `~/src/ht6` if it has a Git remote.

## 3. Start and demo

In the Ubuntu terminal:

```bash
./scripts/kernel-lab-wsl up
./scripts/kernel-lab-wsl demo
```

The first command installs Docker Engine from Docker's official Ubuntu repository, installs the
native systemd services, builds EcoRoute, starts the real node agent, and registers the benchmark
endpoint. The first run takes longer because it downloads packages and builds images. From the
Windows browser, open <http://localhost:3000> and choose **Self-Hosted Nodes**.

The benchmark takes about 70 seconds. Check status or stop the CPU-heavy background workload with:

```bash
./scripts/kernel-lab-wsl status
./scripts/kernel-lab-wsl down
```

If the repository remains on the Windows filesystem, the same actions can be launched from
PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\kernel-lab-windows.ps1 -Action up
powershell -ExecutionPolicy Bypass -File .\scripts\kernel-lab-windows.ps1 -Action demo
```

Pass `-Distro Ubuntu` if that is the name shown by `wsl --list --verbose`.

## What the demo proves

The result shows measured baseline-versus-optimized latency, throughput, inference/background CPU
time, cgroup `cpu.stat` throttling, and the apply/verify/rollback transaction. It proves the policy
changed real WSL2 Linux scheduling behavior under controlled contention.

WSL2 normally does not expose physical package-energy counters. The UI therefore reports energy as
`unavailable`; do not present this run as measured electricity or battery savings.
