[CmdletBinding()]
param(
    [ValidateSet("up", "demo", "status", "down")]
    [string]$Action = "up",
    [string]$Distro = "Ubuntu-24.04",
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$resolvedRepo = (Resolve-Path -LiteralPath $RepoPath).Path
$linuxRepo = (& wsl.exe -d $Distro -- wslpath -a $resolvedRepo).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($linuxRepo)) {
    throw "Could not resolve the repository in WSL distro '$Distro'. Is it installed and running?"
}

& wsl.exe -d $Distro -- bash "$linuxRepo/scripts/kernel-lab-wsl" $Action $linuxRepo
if ($LASTEXITCODE -ne 0) {
    throw "Kernel lab action '$Action' failed in WSL."
}
