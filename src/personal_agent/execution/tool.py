"""Policy-aware local filesystem, shell, and workspace tool adapter."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.execution.docker import (
    DockerSandbox,
    SandboxExecutionError,
    SandboxResult,
    SandboxUnavailableError,
)
from personal_agent.execution.workspace import WorkspaceService
from personal_agent.tools.contracts import ToolEvidence, ToolExecutionResult

_SENSITIVE_ARGUMENT_MARKERS = ("api-key", "apikey", "authorization", "password", "secret", "token")


class ShellArguments(BaseModel):
    command: list[str] = Field(min_length=1)
    network: bool = False


class LocalExecutionTool:
    name = "local_execution"

    def __init__(self, sandbox: DockerSandbox, workspaces: WorkspaceService) -> None:
        self._sandbox = sandbox
        self._workspaces = workspaces

    async def execute(self, action: ActionRequest) -> ToolExecutionResult:
        try:
            return await self._execute(action)
        except (SandboxExecutionError, SandboxUnavailableError, ValueError) as error:
            return ToolExecutionResult(
                tool_name=self.name,
                operation=action.operation,
                success=False,
                error=str(error),
            )

    async def aclose(self) -> None:
        return None

    async def _execute(self, action: ActionRequest) -> ToolExecutionResult:
        if action.operation == "health_check":
            self._require_risk(action, RiskLevel.READ)
            await self._sandbox.health_check()
            return ToolExecutionResult(
                tool_name=self.name,
                operation=action.operation,
                success=True,
                data={"available": True},
            )
        if action.operation == "create_workspace":
            self._require_at_least_write(action)
            name = str(action.arguments.get("name") or action.resource)
            path = await self._workspaces.create_repository(name)
            return ToolExecutionResult(
                tool_name=self.name,
                operation=action.operation,
                success=True,
                data={"path": str(path), "initialized_git": True},
                external_ids=[str(path)],
                evidence=[
                    ToolEvidence(kind="local_workspace", identifier=str(path), title=path.name)
                ],
            )

        workspace = self._workspaces.resolve_workspace(action.resource)
        if action.operation == "list_files":
            self._require_risk(action, RiskLevel.READ)
            result = await self._sandbox.run(
                workspace=workspace,
                command=["find", ".", "-maxdepth", "3", "-type", "f", "-print"],
                writable=False,
            )
            return self._result(action, result, command=["find", ".", "-maxdepth", "3"])
        if action.operation == "read_file":
            self._require_risk(action, RiskLevel.READ)
            relative_path = str(action.arguments.get("path", ""))
            container_path = self._workspaces.container_path(
                workspace, relative_path, writing=False
            )
            command = ["cat", "--", container_path]
            result = await self._sandbox.run(
                workspace=workspace,
                command=command,
                writable=False,
            )
            return self._result(action, result, command=command)
        if action.operation == "write_file":
            self._require_at_least_write(action)
            relative_path = str(action.arguments.get("path", ""))
            content = str(action.arguments.get("content", ""))
            container_path = self._workspaces.container_path(
                workspace, relative_path, writing=True
            )
            command = ["sh", "-c", 'cat > "$1"', "personal-agent", container_path]
            result = await self._sandbox.run(
                workspace=workspace,
                command=command,
                writable=True,
                stdin=content.encode(),
            )
            return self._result(action, result, command=command)
        if action.operation == "run_command":
            self._require_risk(action, RiskLevel.RISKY)
            arguments = ShellArguments.model_validate(action.arguments)
            result = await self._sandbox.run(
                workspace=workspace,
                command=arguments.command,
                writable=True,
                network_enabled=arguments.network,
            )
            return self._result(action, result, command=arguments.command)
        raise ValueError(f"unsupported local execution operation {action.operation!r}")

    @staticmethod
    def _require_risk(action: ActionRequest, required: RiskLevel) -> None:
        if action.risk_level is not required:
            raise ValueError(f"{action.operation} must use {required.value} risk")

    @staticmethod
    def _require_at_least_write(action: ActionRequest) -> None:
        if action.risk_level is RiskLevel.READ:
            raise ValueError(f"{action.operation} cannot use read risk")

    def _result(
        self,
        action: ActionRequest,
        result: SandboxResult,
        *,
        command: list[str],
    ) -> ToolExecutionResult:
        command_digest = hashlib.sha256("\0".join(command).encode()).hexdigest()
        audit_data: dict[str, Any] = {
            "command": self._redact_command(command),
            "command_digest": command_digest,
            "exit_code": result.exit_code,
            "stdout_digest": result.stdout_digest,
            "stderr_digest": result.stderr_digest,
            "output_truncated": result.output_truncated,
            "network_enabled": result.network_enabled,
        }
        return ToolExecutionResult(
            tool_name=self.name,
            operation=action.operation,
            success=result.exit_code == 0,
            data={
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "output_truncated": result.output_truncated,
            },
            audit_data=audit_data,
            error=None if result.exit_code == 0 else f"command exited with code {result.exit_code}",
        )

    @staticmethod
    def _redact_command(command: list[str]) -> list[str]:
        redacted: list[str] = []
        redact_next = False
        for argument in command:
            lowered = argument.lower()
            if redact_next:
                redacted.append("[REDACTED]")
                redact_next = False
                continue
            if argument.startswith("-") and any(
                marker in lowered for marker in _SENSITIVE_ARGUMENT_MARKERS
            ):
                if "=" in argument:
                    redacted.append(f"{argument.split('=', 1)[0]}=[REDACTED]")
                else:
                    redacted.append(argument)
                    redact_next = True
                continue
            if "=" in argument and any(
                marker in lowered.split("=", 1)[0]
                for marker in _SENSITIVE_ARGUMENT_MARKERS
            ):
                redacted.append(f"{argument.split('=', 1)[0]}=[REDACTED]")
                continue
            redacted.append(argument)
        return redacted
