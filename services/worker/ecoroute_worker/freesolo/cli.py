from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Literal

ALLOWED_COMMANDS: dict[str, set[str]] = {
    "env": {"push"},
    "train": {"--dry-run", "--cost", "--background"},
    "status": set(),
    "log": set(),
    "cancel": set(),
    "deploy": {"--dry-run"},
    "export": {"--adapter-id", "--repository"},
}
SECRET_PATTERN = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{6,}|bearer\s+[A-Za-z0-9._-]+|(?:api[_-]?key|token|password)\s*[:=]\s*\S+)"
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def render_config(template: str, substitutions: dict[str, str]) -> str:
    rendered = template
    for key, value in substitutions.items():
        rendered = rendered.replace("${" + key + "}", value)
    unresolved = re.findall(r"\$\{[A-Z0-9_]+\}", rendered)
    if unresolved:
        raise ValueError(f"unresolved FreeSOLO config variables: {', '.join(unresolved)}")
    return rendered


def build_command(
    action: Literal[
        "env_push",
        "train_dry_run",
        "train_cost",
        "train_launch",
        "status",
        "log",
        "cancel",
        "deploy_dry_run",
        "deploy",
        "export",
    ],
    target: str,
    repository: str | None = None,
    name: str | None = None,
) -> list[str]:
    commands = {
        "env_push": ["flash", "env", "push", "--name", name or "", target],
        "train_dry_run": ["flash", "train", target, "--dry-run"],
        "train_cost": ["flash", "train", target, "--cost"],
        "train_launch": ["flash", "train", target, "--background"],
        "status": ["flash", "status", target],
        "log": ["flash", "log", target],
        "cancel": ["flash", "cancel", target],
        "deploy_dry_run": ["flash", "deploy", target, "--dry-run"],
        "deploy": ["flash", "deploy", target],
        "export": ["flash", "export", "--adapter-id", target, "--repository", repository or ""],
    }
    command = commands[action]
    if not all(command) or command[0] != "flash":
        raise ValueError("invalid FreeSOLO command")
    return command


class FreeSoloCli:
    """Strict subprocess adapter. No call is made until a confirmed worker action invokes it."""

    def __init__(self, api_key: str, organization: str = "") -> None:
        self.api_key = api_key
        self.organization = organization

    async def run(self, command: list[str], timeout_seconds: int = 60) -> CommandResult:
        if not self.api_key:
            raise RuntimeError("FreeSOLO is not configured")
        if not _command_allowed(command):
            raise ValueError("FreeSOLO command is not allowlisted")
        env = {**os.environ, "FREESOLO_API_KEY": self.api_key}
        if self.organization:
            env["FREESOLO_ORG"] = self.organization
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        return CommandResult(
            process.returncode or 0,
            _redact(stdout.decode(errors="replace"))[:1_000_000],
            _redact(stderr.decode(errors="replace"))[:1_000_000],
        )


def _redact(value: str) -> str:
    return SECRET_PATTERN.sub("[REDACTED]", value)


def _command_allowed(command: list[str]) -> bool:
    if len(command) < 2 or command[0] != "flash":
        return False
    subcommand = command[1]
    if subcommand not in ALLOWED_COMMANDS:
        return False
    if subcommand == "env":
        return (
            len(command) == 6
            and command[2:4] == ["push", "--name"]
            and bool(command[4])
            and bool(command[5])
        )
    if subcommand == "train":
        return len(command) == 4 and command[3] in ALLOWED_COMMANDS["train"]
    if subcommand in {"status", "log", "cancel", "deploy"}:
        return len(command) in {3, 4} and (
            len(command) == 3 or command[3] in ALLOWED_COMMANDS[subcommand]
        )
    if subcommand == "export":
        return len(command) == 6 and command[2] == "--adapter-id" and command[4] == "--repository"
    return False
