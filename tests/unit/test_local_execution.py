"""Tests for fail-closed, path-constrained Docker execution."""

from pathlib import Path
from typing import cast

import pytest

from personal_agent.core.config import LocalExecutionSettings
from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.execution import (
    DockerSandbox,
    LocalExecutionTool,
    SandboxExecutionError,
    SandboxUnavailableError,
    WorkspaceService,
)
from personal_agent.execution.docker import CommandExecutor, ProcessResult, SandboxResult


class FakeExecutor:
    def __init__(self, results: list[ProcessResult]) -> None:
        self.results = results
        self.calls: list[
            tuple[list[str], bytes | None, dict[str, str] | None, float]
        ] = []

    async def execute(
        self,
        arguments: list[str],
        *,
        stdin: bytes | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float,
    ) -> ProcessResult:
        self.calls.append((arguments, stdin, environment, timeout_seconds))
        return self.results.pop(0)


class FakeSandbox:
    def __init__(self, result: SandboxResult | None = None) -> None:
        self.result = result or SandboxResult(
            exit_code=0,
            stdout="ok\n",
            stderr="",
            stdout_digest="stdout-hash",
            stderr_digest="stderr-hash",
            output_truncated=False,
            network_enabled=False,
        )
        self.calls: list[dict[str, object]] = []

    async def health_check(self) -> None:
        return None

    async def run(self, **arguments: object) -> SandboxResult:
        self.calls.append(arguments)
        return self.result


def settings(workspace_root: Path) -> LocalExecutionSettings:
    return LocalExecutionSettings(
        enabled=True,
        workspace_root=workspace_root,
        docker_image="sandbox:test",
        max_output_bytes=1_024,
    )


async def test_docker_runtime_fails_closed_when_daemon_is_unhealthy(tmp_path: Path) -> None:
    executor = FakeExecutor([ProcessResult(exit_code=1, stdout=b"", stderr=b"failed")])
    sandbox = DockerSandbox(
        settings(tmp_path),
        executor=cast(CommandExecutor, executor),
    )

    with pytest.raises(SandboxUnavailableError, match="daemon"):
        await sandbox.health_check()

    assert executor.calls[0][0][0:2] == ["docker", "version"]


async def test_docker_runtime_uses_hardened_offline_container(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    executor = FakeExecutor(
        [
            ProcessResult(exit_code=0, stdout=b"27", stderr=b""),
            ProcessResult(exit_code=0, stdout=b"[]", stderr=b""),
            ProcessResult(exit_code=0, stdout=b"x" * 1_025, stderr=b"warning"),
        ]
    )
    sandbox = DockerSandbox(
        settings(tmp_path),
        executor=cast(CommandExecutor, executor),
    )

    result = await sandbox.run(
        workspace=workspace,
        command=["python", "-V"],
        writable=False,
    )

    docker_run = executor.calls[2][0]
    assert docker_run[:2] == ["docker", "run"]
    assert docker_run[docker_run.index("--network") + 1] == "none"
    assert "--read-only" in docker_run
    assert "no-new-privileges" in docker_run
    assert f"type=bind,src={workspace.resolve()},dst=/workspace,readonly" in docker_run
    assert result.stdout == "x" * 1_024
    assert result.output_truncated is True
    assert len(result.stdout_digest) == 64


async def test_docker_runtime_enables_network_only_when_requested(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    executor = FakeExecutor(
        [
            ProcessResult(exit_code=0, stdout=b"27", stderr=b""),
            ProcessResult(exit_code=0, stdout=b"[]", stderr=b""),
            ProcessResult(exit_code=0, stdout=b"", stderr=b""),
        ]
    )
    sandbox = DockerSandbox(
        settings(tmp_path),
        executor=cast(CommandExecutor, executor),
    )

    await sandbox.run(
        workspace=workspace,
        command=["python", "-V"],
        writable=True,
        network_enabled=True,
    )

    docker_run = executor.calls[2][0]
    assert docker_run[docker_run.index("--network") + 1] == "bridge"
    assert f"type=bind,src={workspace.resolve()},dst=/workspace" in docker_run


async def test_docker_runtime_inherits_named_environment_without_argument_values(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    executor = FakeExecutor(
        [
            ProcessResult(exit_code=0, stdout=b"27", stderr=b""),
            ProcessResult(exit_code=0, stdout=b"[]", stderr=b""),
            ProcessResult(exit_code=0, stdout=b"", stderr=b""),
        ]
    )
    sandbox = DockerSandbox(
        settings(tmp_path),
        executor=cast(CommandExecutor, executor),
    )

    await sandbox.run(
        workspace=workspace,
        command=["python", "-V"],
        writable=False,
        environment={"DEEPSEEK_API_KEY": "secret-value"},
    )

    docker_run = executor.calls[2][0]
    assert docker_run[docker_run.index("--env") + 1] == "DEEPSEEK_API_KEY"
    assert "secret-value" not in docker_run
    assert executor.calls[2][2] == {"DEEPSEEK_API_KEY": "secret-value"}


async def test_docker_runtime_accepts_exact_named_repository(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    root.mkdir()
    named_repo = tmp_path / "named"
    named_repo.mkdir()
    executor = FakeExecutor(
        [
            ProcessResult(exit_code=0, stdout=b"27", stderr=b""),
            ProcessResult(exit_code=0, stdout=b"[]", stderr=b""),
            ProcessResult(exit_code=0, stdout=b"", stderr=b""),
        ]
    )
    configured = settings(root).model_copy(update={"repository_paths": [named_repo]})
    sandbox = DockerSandbox(configured, executor=cast(CommandExecutor, executor))

    await sandbox.run(workspace=named_repo, command=["git", "status"], writable=False)

    assert f"type=bind,src={named_repo.resolve()},dst=/workspace,readonly" in executor.calls[2][0]


def test_workspace_service_rejects_escape_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    workspace = root / "repo"
    outside = tmp_path / "outside"
    workspace.mkdir(parents=True)
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    service = WorkspaceService(root, cast(DockerSandbox, FakeSandbox()))

    with pytest.raises(SandboxExecutionError, match="outside"):
        service.resolve_workspace(str(outside))
    with pytest.raises(SandboxExecutionError, match="escapes"):
        service.container_path(workspace, "escape/secret.txt", writing=True)
    with pytest.raises(SandboxExecutionError, match="relative"):
        service.container_path(workspace, "../secret.txt", writing=False)


async def test_workspace_creation_initializes_git_below_root(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    service = WorkspaceService(tmp_path / "workspaces", cast(DockerSandbox, sandbox))

    created = await service.create_repository("new-agent")

    assert created == (tmp_path / "workspaces" / "new-agent").resolve()
    assert sandbox.calls[0]["workspace"] == created
    assert sandbox.calls[0]["command"] == ["git", "init", "--initial-branch=main", "."]
    with pytest.raises(SandboxExecutionError, match="invalid"):
        await service.create_repository("../escape")


async def test_local_tool_requires_risky_approval_for_every_command(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sandbox = FakeSandbox()
    tool = LocalExecutionTool(
        cast(DockerSandbox, sandbox),
        WorkspaceService(tmp_path, cast(DockerSandbox, sandbox)),
    )

    network_result = await tool.execute(
        ActionRequest(
            tool_name="local_execution",
            operation="run_command",
            resource=str(workspace),
            risk_level=RiskLevel.WRITE,
            summary="Fetch dependencies",
            arguments={"command": ["python", "script.py"], "network": True},
        )
    )
    offline_result = await tool.execute(
        ActionRequest(
            tool_name="local_execution",
            operation="run_command",
            resource=str(workspace),
            risk_level=RiskLevel.WRITE,
            summary="Run tests",
            arguments={"command": ["pytest"]},
        )
    )

    assert network_result.success is False
    assert "risky" in (network_result.error or "")
    assert offline_result.success is False
    assert "risky" in (offline_result.error or "")
    assert sandbox.calls == []


async def test_local_tool_returns_command_audit_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sandbox = FakeSandbox()
    tool = LocalExecutionTool(
        cast(DockerSandbox, sandbox),
        WorkspaceService(tmp_path, cast(DockerSandbox, sandbox)),
    )
    action = ActionRequest(
        tool_name="local_execution",
        operation="run_command",
        resource=str(workspace),
        risk_level=RiskLevel.RISKY,
        summary="Install package",
        arguments={"command": ["pip", "install", "example"], "network": True},
    )

    result = await tool.execute(action)

    assert result.success is True
    assert result.audit_data["command"] == ["pip", "install", "example"]
    assert result.audit_data["exit_code"] == 0
    assert result.audit_data["stdout_digest"] == "stdout-hash"
    assert sandbox.calls[0]["network_enabled"] is True
