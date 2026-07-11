"""Fail-closed Docker runtime for host-local agent actions."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from personal_agent.core.config import LocalExecutionSettings


class SandboxUnavailableError(RuntimeError):
    """Raised when Docker or the configured sandbox image is unavailable."""


class SandboxExecutionError(RuntimeError):
    """Raised when a sandbox request violates the runtime boundary."""


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    stdout: bytes
    stderr: bytes


class CommandExecutor(Protocol):
    async def execute(
        self,
        arguments: list[str],
        *,
        stdin: bytes | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float,
    ) -> ProcessResult: ...


class AsyncioCommandExecutor:
    """Execute the Docker CLI without invoking a host shell."""

    async def execute(
        self,
        arguments: list[str],
        *,
        stdin: bytes | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float,
    ) -> ProcessResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **(environment or {})},
            )
        except (FileNotFoundError, PermissionError) as error:
            raise SandboxUnavailableError("Docker CLI is unavailable") from error
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin),
                timeout=timeout_seconds,
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise SandboxExecutionError("sandbox command timed out") from error
        return ProcessResult(
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    stdout_digest: str
    stderr_digest: str
    output_truncated: bool
    network_enabled: bool


class DockerSandbox:
    """Run one command in a short-lived, resource-constrained container."""

    def __init__(
        self,
        settings: LocalExecutionSettings,
        *,
        executor: CommandExecutor | None = None,
    ) -> None:
        self._settings = settings
        self._executor = executor or AsyncioCommandExecutor()
        self._healthy = False

    async def health_check(self) -> None:
        if self._healthy:
            return
        version = await self._executor.execute(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            timeout_seconds=min(self._settings.command_timeout_seconds, 10.0),
        )
        if version.exit_code != 0:
            raise SandboxUnavailableError("Docker daemon is unavailable")
        image = await self._executor.execute(
            ["docker", "image", "inspect", self._settings.docker_image],
            timeout_seconds=min(self._settings.command_timeout_seconds, 10.0),
        )
        if image.exit_code != 0:
            raise SandboxUnavailableError(
                f"sandbox image {self._settings.docker_image!r} is unavailable"
            )
        self._healthy = True

    async def run(
        self,
        *,
        workspace: Path,
        command: list[str],
        writable: bool,
        network_enabled: bool = False,
        stdin: bytes | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> SandboxResult:
        if not command or any("\x00" in argument for argument in command):
            raise SandboxExecutionError("sandbox command is invalid")
        workspace_path = self._resolve_workspace(workspace)
        container_environment = self._validate_environment(environment or {})
        await self.health_check()
        mount_spec = f"type=bind,src={workspace_path},dst=/workspace"
        if not writable:
            mount_spec = f"{mount_spec},readonly"
        network_mode = "bridge" if network_enabled else "none"
        arguments = [
            "docker",
            "run",
            "--rm",
            "--init",
            "--name",
            f"personal-agent-{uuid4().hex[:12]}",
            "--network",
            network_mode,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self._settings.pids_limit),
            "--memory",
            self._settings.memory_limit,
            "--cpus",
            str(self._settings.cpu_limit),
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            mount_spec,
            "--workdir",
            "/workspace",
        ]
        for name in sorted(container_environment):
            arguments.extend(["--env", name])
        arguments.extend([self._settings.docker_image, *command])
        process_result = await self._executor.execute(
            arguments,
            stdin=stdin,
            environment=container_environment,
            timeout_seconds=timeout_seconds or self._settings.command_timeout_seconds,
        )
        output_limit = self._settings.max_output_bytes
        stdout = process_result.stdout
        stderr = process_result.stderr
        truncated = len(stdout) > output_limit or len(stderr) > output_limit
        return SandboxResult(
            exit_code=process_result.exit_code,
            stdout=stdout[:output_limit].decode(errors="replace"),
            stderr=stderr[:output_limit].decode(errors="replace"),
            stdout_digest=hashlib.sha256(stdout).hexdigest(),
            stderr_digest=hashlib.sha256(stderr).hexdigest(),
            output_truncated=truncated,
            network_enabled=network_enabled,
        )

    def _resolve_workspace(self, workspace: Path) -> Path:
        root = self._settings.workspace_root.resolve()
        try:
            candidate = workspace.expanduser().resolve(strict=True)
        except FileNotFoundError as error:
            raise SandboxExecutionError("workspace does not exist") from error
        named_repositories = {
            path.resolve() for path in self._settings.repository_paths if path.exists()
        }
        allowed = candidate.is_relative_to(root) or candidate in named_repositories
        if not candidate.is_dir() or not allowed:
            raise SandboxExecutionError("workspace is outside the configured workspace root")
        return candidate

    @staticmethod
    def _validate_environment(environment: dict[str, str]) -> dict[str, str]:
        for name, value in environment.items():
            if re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) is None or "\x00" in value:
                raise SandboxExecutionError("sandbox environment is invalid")
        return environment
