"""Restricted Codex CLI subprocess boundary for subscription-backed inference."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from personal_agent.core.config import CodexSubscriptionSettings
from personal_agent.models.codex_cli_contract import (
    CodexCliContractError,
    CodexCliFailure,
    classify_codex_cli_failure,
    parse_jsonl_events,
    require_supported_version,
    validate_exec_help,
)
from personal_agent.observability import get_logger

_DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode_host",
    "computer_use",
    "goals",
    "hooks",
    "image_generation",
    "in_app_browser",
    "multi_agent",
    "plugins",
    "remote_plugin",
    "shell_tool",
    "skill_mcp_dependency_install",
    "tool_call_mcp_elicitation",
    "unified_exec",
    "workspace_dependencies",
)
_ENVIRONMENT_ALLOWLIST = (
    "CODEX_HOME",
    "HOME",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
)


@dataclass(frozen=True)
class CodexProcessResult:
    exit_code: int
    stdout: bytes
    stderr: bytes


class CodexProcessExecutor(Protocol):
    async def execute(
        self,
        arguments: list[str],
        *,
        stdin: bytes | None,
        environment: dict[str, str],
        timeout_seconds: float,
        output_limit: int,
    ) -> CodexProcessResult: ...


class AsyncioCodexProcessExecutor:
    """Run Codex without a host shell and terminate the complete process group."""

    async def execute(
        self,
        arguments: list[str],
        *,
        stdin: bytes | None,
        environment: dict[str, str],
        timeout_seconds: float,
        output_limit: int,
    ) -> CodexProcessResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise CodexCliProviderError(CodexCliFailure.MISSING_EXECUTABLE) from error

        async def read_limited(stream: asyncio.StreamReader | None) -> bytes:
            if stream is None:
                return b""
            chunks: list[bytes] = []
            size = 0
            while chunk := await stream.read(16_384):
                size += len(chunk)
                if size > output_limit:
                    raise CodexCliProviderError(CodexCliFailure.MALFORMED_OUTPUT)
                chunks.append(chunk)
            return b"".join(chunks)

        try:
            if stdin is not None and process.stdin is not None:
                process.stdin.write(stdin)
                await process.stdin.drain()
                process.stdin.close()
            stdout, stderr, exit_code = await asyncio.wait_for(
                asyncio.gather(
                    read_limited(process.stdout),
                    read_limited(process.stderr),
                    process.wait(),
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError as error:
            self._kill_process_group(process)
            await process.wait()
            raise CodexCliProviderError(CodexCliFailure.TIMEOUT) from error
        except BaseException:
            self._kill_process_group(process)
            await process.wait()
            raise
        return CodexProcessResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    @staticmethod
    def _kill_process_group(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return


@dataclass(frozen=True)
class CodexInvocationRecord:
    provider: str
    cli_version: str
    model: str
    duration_ms: int
    exit_class: str
    retry_count: int
    response_digest: str | None


CodexObserver = Callable[[CodexInvocationRecord], Awaitable[None]]


class CodexCliProviderError(RuntimeError):
    """Sanitized provider failure safe for fallback and user display."""

    def __init__(self, failure: CodexCliFailure) -> None:
        self.failure = failure
        super().__init__(self._message(failure))

    @staticmethod
    def _message(failure: CodexCliFailure) -> str:
        if failure is CodexCliFailure.MISSING_LOGIN:
            return "Codex CLI is not logged in; run `codex login --device-auth`"
        if failure is CodexCliFailure.MISSING_EXECUTABLE:
            return "Codex CLI executable was not found"
        return f"Codex subscription provider failed: {failure.value}"


class CodexCliRunner:
    """Probe and invoke a restricted subscription-backed Codex CLI."""

    def __init__(
        self,
        settings: CodexSubscriptionSettings,
        *,
        executor: CodexProcessExecutor | None = None,
        observer: CodexObserver | None = None,
    ) -> None:
        self._settings = settings
        self._executor = executor or AsyncioCodexProcessExecutor()
        self._observer = observer or self._log_record
        self._version: str | None = None

    async def health_check(self) -> str:
        if self._version is not None:
            return self._version
        version_result = await self._execute_probe([self._settings.executable, "--version"])
        version_output = version_result.stdout.decode(errors="replace").strip()
        try:
            require_supported_version(version_output)
        except CodexCliContractError as error:
            raise CodexCliProviderError(CodexCliFailure.UNSUPPORTED_VERSION) from error

        help_result = await self._execute_probe(
            [self._settings.executable, "exec", "--help"]
        )
        try:
            validate_exec_help(help_result.stdout.decode(errors="replace"))
        except CodexCliContractError as error:
            raise CodexCliProviderError(CodexCliFailure.UNSUPPORTED_VERSION) from error

        login_result = await self._executor.execute(
            [self._settings.executable, "login", "status"],
            stdin=None,
            environment=self._environment(),
            timeout_seconds=min(self._settings.timeout_seconds, 10.0),
            output_limit=self._settings.max_response_bytes,
        )
        if login_result.exit_code != 0:
            failure = classify_codex_cli_failure(
                exit_code=login_result.exit_code,
                stderr=(login_result.stderr + login_result.stdout).decode(errors="replace"),
            )
            raise CodexCliProviderError(failure or CodexCliFailure.PROCESS_FAILURE)
        self._version = version_output
        return version_output

    async def invoke(
        self,
        *,
        prompt: str,
        output_schema: dict[str, Any],
        model: str,
        retry_count: int,
    ) -> dict[str, Any]:
        version = await self.health_check()
        self._settings.working_directory.mkdir(parents=True, exist_ok=True)
        started = monotonic()
        result: CodexProcessResult | None = None
        failure: CodexCliFailure | None = None
        response_digest: str | None = None
        try:
            with tempfile.TemporaryDirectory(
                dir=self._settings.working_directory,
                prefix="request-",
            ) as temporary_directory:
                directory = Path(temporary_directory)
                schema_path = directory / "response.schema.json"
                response_path = directory / "response.json"
                schema_path.write_text(json.dumps(output_schema, separators=(",", ":")))
                command = self._command(
                    model=model,
                    directory=directory,
                    schema_path=schema_path,
                    response_path=response_path,
                )
                result = await self._executor.execute(
                    command,
                    stdin=prompt.encode(),
                    environment=self._environment(),
                    timeout_seconds=self._settings.timeout_seconds,
                    output_limit=self._settings.max_response_bytes,
                )
                if result.exit_code != 0:
                    failure = classify_codex_cli_failure(
                        exit_code=result.exit_code,
                        stderr=result.stderr.decode(errors="replace")[
                            -self._settings.max_stderr_chars :
                        ],
                    )
                    raise CodexCliProviderError(
                        failure or CodexCliFailure.PROCESS_FAILURE
                    )
                try:
                    parse_jsonl_events(result.stdout.decode(errors="replace"))
                    response_bytes = response_path.read_bytes()
                    if len(response_bytes) > self._settings.max_response_bytes:
                        raise CodexCliContractError("Codex response exceeded the limit")
                    payload = json.loads(response_bytes)
                    if not isinstance(payload, dict):
                        raise CodexCliContractError("Codex response must be an object")
                except (CodexCliContractError, json.JSONDecodeError, OSError) as error:
                    failure = CodexCliFailure.MALFORMED_OUTPUT
                    raise CodexCliProviderError(failure) from error
                response_digest = hashlib.sha256(response_bytes).hexdigest()
                return payload
        except CodexCliProviderError as error:
            failure = error.failure
            raise
        finally:
            await self._observer(
                CodexInvocationRecord(
                    provider="codex-subscription",
                    cli_version=version,
                    model=model,
                    duration_ms=int((monotonic() - started) * 1000),
                    exit_class=(failure.value if failure else "success"),
                    retry_count=retry_count,
                    response_digest=response_digest,
                )
            )

    async def _execute_probe(self, command: list[str]) -> CodexProcessResult:
        result = await self._executor.execute(
            command,
            stdin=None,
            environment=self._environment(),
            timeout_seconds=min(self._settings.timeout_seconds, 10.0),
            output_limit=self._settings.max_response_bytes,
        )
        if result.exit_code != 0:
            raise CodexCliProviderError(CodexCliFailure.PROCESS_FAILURE)
        return result

    def _command(
        self,
        *,
        model: str,
        directory: Path,
        schema_path: Path,
        response_path: Path,
    ) -> list[str]:
        command = [
            self._settings.executable,
            "exec",
            "--model",
            model,
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(response_path),
            "--json",
            "--color",
            "never",
            "--cd",
            str(directory),
        ]
        for feature in _DISABLED_FEATURES:
            command.extend(["--disable", feature])
        command.append("-")
        return command

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            name: os.environ[name]
            for name in _ENVIRONMENT_ALLOWLIST
            if name in os.environ
        }

    @staticmethod
    async def _log_record(record: CodexInvocationRecord) -> None:
        get_logger(__name__).info("codex_subscription.invocation", **record.__dict__)
