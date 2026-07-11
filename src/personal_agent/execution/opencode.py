"""Sandboxed OpenCode coding delegation with Git and test evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from personal_agent.core.config import OpenCodeSettings
from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.execution.docker import (
    DockerSandbox,
    SandboxExecutionError,
    SandboxResult,
    SandboxUnavailableError,
)
from personal_agent.execution.workspace import WorkspaceService
from personal_agent.tools.contracts import ToolEvidence, ToolExecutionResult


class CodingTaskContract(BaseModel):
    """Structured task sent to the coding worker."""

    task: str = Field(min_length=1, max_length=12_000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    expected_files: list[str] = Field(default_factory=list, max_length=50)
    test_commands: list[list[str]] = Field(default_factory=list, max_length=10)


class CodingCommandResult(BaseModel):
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    stdout_digest: str
    stderr_digest: str
    output_truncated: bool


class CodingEvidence(BaseModel):
    workspace: str
    model: str
    changed_files: list[str]
    diff_summary: str
    diff: str
    tests: list[CodingCommandResult]
    report: str
    baseline_dirty: bool
    requested_change_verified: bool


class OpenCodeTool:
    """Execute one approved coding task in an explicitly named repository."""

    name = "opencode"

    def __init__(
        self,
        *,
        settings: OpenCodeSettings,
        api_key: str,
        sandbox: DockerSandbox,
        workspaces: WorkspaceService,
    ) -> None:
        self._settings = settings
        self._api_key = api_key
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
        if action.operation != "code_task":
            raise ValueError(f"unsupported OpenCode operation {action.operation!r}")
        if action.risk_level is not RiskLevel.RISKY:
            raise ValueError("OpenCode tasks require risky per-action approval")
        workspace = self._workspaces.resolve_workspace(action.resource)
        if not (workspace / ".git").is_dir():
            raise ValueError("OpenCode workspace must be a Git repository")
        contract = CodingTaskContract.model_validate(action.arguments)
        for command in contract.test_commands:
            self._validate_test_command(command)

        before_status = await self._git(workspace, ["status", "--porcelain=v1"])
        before_diff = await self._git(workspace, ["diff", "--no-ext-diff", "HEAD"])
        command_audits = [
            self._command_audit(["git", "status", "--porcelain=v1"], before_status),
            self._command_audit(["git", "diff", "--no-ext-diff", "HEAD"], before_diff),
        ]
        prompt = self._task_prompt(contract)
        opencode_command = [
            self._settings.executable,
            "--pure",
            "run",
            "--format",
            "json",
            "--model",
            self._settings.model,
            prompt,
        ]
        opencode_result = await self._sandbox.run(
            workspace=workspace,
            command=opencode_command,
            writable=True,
            network_enabled=True,
            environment=self._environment(),
            timeout_seconds=self._settings.timeout_seconds,
        )
        command_audits.append(
            self._command_audit(
                opencode_command[:-1] + ["[TASK_CONTRACT]"],
                opencode_result,
            )
        )
        report = self._extract_report(opencode_result.stdout)
        if opencode_result.exit_code != 0:
            return self._failed_result(
                action,
                opencode_result,
                report=report,
                error=f"OpenCode exited with code {opencode_result.exit_code}",
                command_audits=command_audits,
            )

        after_status = await self._git(workspace, ["status", "--porcelain=v1"])
        after_diff = await self._git(workspace, ["diff", "--no-ext-diff", "HEAD"])
        diff_summary = await self._git(workspace, ["diff", "--stat", "HEAD"])
        command_audits.extend(
            [
                self._command_audit(["git", "status", "--porcelain=v1"], after_status),
                self._command_audit(
                    ["git", "diff", "--no-ext-diff", "HEAD"], after_diff
                ),
                self._command_audit(["git", "diff", "--stat", "HEAD"], diff_summary),
            ]
        )
        changed_files = self._parse_changed_files(after_status.stdout)
        changed = self._snapshot_digest(before_status, before_diff) != self._snapshot_digest(
            after_status, after_diff
        )
        expected = set(contract.expected_files)
        requested_change_verified = changed and expected.issubset(changed_files)

        tests: list[CodingCommandResult] = []
        for command in contract.test_commands:
            test_result = await self._sandbox.run(
                workspace=workspace,
                command=command,
                writable=True,
                network_enabled=False,
            )
            tests.append(self._command_result(command, test_result))
            command_audits.append(self._command_audit(command, test_result))
        tests_passed = all(test.exit_code == 0 for test in tests)
        evidence = CodingEvidence(
            workspace=str(workspace),
            model=self._settings.model,
            changed_files=sorted(changed_files),
            diff_summary=diff_summary.stdout[: self._settings.max_diff_chars],
            diff=after_diff.stdout[: self._settings.max_diff_chars],
            tests=tests,
            report=report,
            baseline_dirty=bool(before_status.stdout.strip()),
            requested_change_verified=requested_change_verified,
        )
        success = requested_change_verified and tests_passed
        error: str | None = None
        if not requested_change_verified:
            error = "requested file changes could not be verified"
        elif not tests_passed:
            error = "one or more requested test commands failed"
        return ToolExecutionResult(
            tool_name=self.name,
            operation=action.operation,
            success=success,
            data=evidence.model_dump(mode="json"),
            external_ids=sorted(changed_files),
            evidence=[
                ToolEvidence(kind="changed_file", identifier=path, title=path)
                for path in sorted(changed_files)
            ],
            audit_data={"commands": command_audits},
            error=error,
        )

    async def _git(self, workspace: Path, arguments: list[str]) -> SandboxResult:
        result = await self._sandbox.run(
            workspace=workspace,
            command=["git", *arguments],
            writable=False,
            network_enabled=False,
        )
        if result.exit_code not in {0, 128}:
            raise SandboxExecutionError(f"Git evidence command failed with {result.exit_code}")
        return result

    def _environment(self) -> dict[str, str]:
        config: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json",
            "model": self._settings.model,
            "autoupdate": False,
            "permission": {
                "*": "deny",
                "read": {
                    "*": "allow",
                    "*.env": "deny",
                    "*.env.*": "deny",
                    "*.env.example": "allow",
                },
                "glob": "allow",
                "grep": "allow",
                "edit": {
                    "*": "allow",
                    ".git/**": "deny",
                    "*.env": "deny",
                    "*.env.*": "deny",
                    "*.env.example": "allow",
                },
                "bash": "deny",
                "task": "deny",
                "webfetch": "deny",
                "websearch": "deny",
                "external_directory": "deny",
            },
        }
        return {
            "DEEPSEEK_API_KEY": self._api_key,
            "HOME": "/tmp/opencode",
            "OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "OPENCODE_DISABLE_CLAUDE_CODE": "true",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
            "XDG_CACHE_HOME": "/tmp/opencode/cache",
            "XDG_CONFIG_HOME": "/tmp/opencode/config",
            "XDG_DATA_HOME": "/tmp/opencode/data",
        }

    @staticmethod
    def _task_prompt(contract: CodingTaskContract) -> str:
        payload = json.dumps(contract.model_dump(mode="json"), indent=2)
        return (
            "Implement the following structured coding task inside the current repository. "
            "Do not change branches, commit, install dependencies, access the network, or touch "
            "paths outside the repository. Do not run shell commands; the parent agent runs the "
            f"requested tests after your edits.\n\nTask contract:\n{payload}"
        )

    def _extract_report(self, output: str) -> str:
        candidates: list[str] = []
        for line in output.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._collect_text(payload, candidates)
        report = candidates[-1] if candidates else output
        return report[-self._settings.max_report_chars :]

    @classmethod
    def _collect_text(cls, value: object, candidates: list[str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"text", "content"} and isinstance(item, str) and item.strip():
                    candidates.append(item.strip())
                else:
                    cls._collect_text(item, candidates)
        elif isinstance(value, list):
            for item in value:
                cls._collect_text(item, candidates)

    @staticmethod
    def _parse_changed_files(status: str) -> set[str]:
        changed: set[str] = set()
        for line in status.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]
            if " -> " in path:
                path = path.rsplit(" -> ", 1)[1]
            changed.add(path.strip('"'))
        return changed

    @staticmethod
    def _snapshot_digest(status: SandboxResult, diff: SandboxResult) -> str:
        return hashlib.sha256(f"{status.stdout}\0{diff.stdout}".encode()).hexdigest()

    @staticmethod
    def _validate_test_command(command: list[str]) -> None:
        if not command:
            raise ValueError("test commands cannot be empty")
        executable = Path(command[0]).name
        allowed = executable in {"pytest", "ruff", "mypy", "cargo", "go"}
        allowed = allowed or (
            executable in {"python", "python3"} and command[1:3] == ["-m", "pytest"]
        )
        allowed = allowed or (
            executable in {"npm", "pnpm", "yarn"}
            and len(command) >= 2
            and (
                command[1] in {"test", "build", "lint", "typecheck"}
                or (
                    len(command) >= 3
                    and command[1] == "run"
                    and command[2] in {"test", "build", "lint", "typecheck"}
                )
            )
        )
        if not allowed:
            raise ValueError(f"test command {executable!r} is not allowed")

    @staticmethod
    def _command_result(command: list[str], result: SandboxResult) -> CodingCommandResult:
        return CodingCommandResult(
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            stdout_digest=result.stdout_digest,
            stderr_digest=result.stderr_digest,
            output_truncated=result.output_truncated,
        )

    @staticmethod
    def _command_audit(command: list[str], result: SandboxResult) -> dict[str, Any]:
        return {
            "command": command,
            "exit_code": result.exit_code,
            "stdout_digest": result.stdout_digest,
            "stderr_digest": result.stderr_digest,
            "network_enabled": result.network_enabled,
            "output_truncated": result.output_truncated,
        }

    def _failed_result(
        self,
        action: ActionRequest,
        result: SandboxResult,
        *,
        report: str,
        error: str,
        command_audits: list[dict[str, Any]],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=self.name,
            operation=action.operation,
            success=False,
            data={"report": report, "stdout": result.stdout, "stderr": result.stderr},
            audit_data={"commands": command_audits},
            error=error,
        )
