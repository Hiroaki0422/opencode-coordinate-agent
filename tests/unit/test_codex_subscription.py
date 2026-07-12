"""Offline tests for restricted Codex subscription coordination."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from personal_agent.core.config import CodexSubscriptionSettings
from personal_agent.models import (
    CodexCliFailure,
    CodexCliProviderError,
    CodexCliRunner,
    CodexInvocationRecord,
    CodexSubscriptionCoordinator,
    CoordinatorDecision,
    FallbackCoordinator,
    GroundedResponse,
)
from personal_agent.models.codex_cli import (
    AsyncioCodexProcessExecutor,
    CodexProcessExecutor,
    CodexProcessResult,
)

EXEC_HELP = " ".join(
    [
        "--sandbox read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--output-schema",
        "--json",
        "--output-last-message",
    ]
)


class FakeExecutor:
    def __init__(
        self,
        responses: list[tuple[CodexProcessResult, dict[str, Any] | None]],
    ) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        arguments: list[str],
        *,
        stdin: bytes | None,
        environment: dict[str, str],
        timeout_seconds: float,
        output_limit: int,
    ) -> CodexProcessResult:
        self.calls.append(
            {
                "arguments": arguments,
                "stdin": stdin,
                "environment": environment,
                "timeout_seconds": timeout_seconds,
                "output_limit": output_limit,
            }
        )
        result, payload = self.responses.pop(0)
        if payload is not None:
            response_path = Path(
                arguments[arguments.index("--output-last-message") + 1]
            )
            response_path.write_text(__import__("json").dumps(payload))
        return result


def process_result(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CodexProcessResult:
    return CodexProcessResult(
        exit_code=exit_code,
        stdout=stdout.encode(),
        stderr=stderr.encode(),
    )


def healthy_responses() -> list[tuple[CodexProcessResult, dict[str, Any] | None]]:
    return [
        (process_result(stdout="codex-cli 0.144.0-alpha.4"), None),
        (process_result(stdout=EXEC_HELP), None),
        (process_result(stdout="Logged in using ChatGPT"), None),
    ]


async def test_runner_uses_restricted_command_and_clean_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    records: list[CodexInvocationRecord] = []

    async def observe(record: CodexInvocationRecord) -> None:
        records.append(record)

    executor = FakeExecutor(
        [
            *healthy_responses(),
            (
                process_result(stdout='{"type":"turn.completed"}\n'),
                {"message": "Hello", "action": None},
            ),
        ]
    )
    settings = CodexSubscriptionSettings(
        enabled=True,
        working_directory=tmp_path,
    )
    runner = CodexCliRunner(
        settings,
        executor=cast(CodexProcessExecutor, executor),
        observer=observe,
    )

    payload = await runner.invoke(
        prompt="Say hello",
        output_schema=CoordinatorDecision.model_json_schema(),
        model="gpt-5.4",
        retry_count=0,
    )

    assert payload["message"] == "Hello"
    invocation = executor.calls[-1]
    command = invocation["arguments"]
    assert invocation["stdin"] == b"Say hello"
    assert "Say hello" not in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    for feature in ("shell_tool", "unified_exec", "plugins", "multi_agent"):
        assert ["--disable", feature] == command[
            command.index(feature) - 1 : command.index(feature) + 1
        ]
    assert "OPENAI_API_KEY" not in invocation["environment"]
    assert records[0].exit_class == "success"
    assert records[0].response_digest


async def test_runner_requires_chatgpt_login(tmp_path: Path) -> None:
    executor = FakeExecutor(
        [
            *healthy_responses()[:2],
            (process_result(exit_code=1, stdout="Not logged in"), None),
        ]
    )
    runner = CodexCliRunner(
        CodexSubscriptionSettings(enabled=True, working_directory=tmp_path),
        executor=cast(CodexProcessExecutor, executor),
    )

    with pytest.raises(CodexCliProviderError) as error:
        await runner.health_check()

    assert error.value.failure is CodexCliFailure.MISSING_LOGIN
    assert "device-auth" in str(error.value)


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("subscription usage limit reached", CodexCliFailure.SUBSCRIPTION_EXHAUSTED),
        ("HTTP 429 rate limit", CodexCliFailure.RATE_LIMITED),
    ],
)
async def test_runner_classifies_provider_failures(
    tmp_path: Path,
    stderr: str,
    expected: CodexCliFailure,
) -> None:
    executor = FakeExecutor(
        [*healthy_responses(), (process_result(exit_code=1, stderr=stderr), None)]
    )
    runner = CodexCliRunner(
        CodexSubscriptionSettings(enabled=True, working_directory=tmp_path),
        executor=cast(CodexProcessExecutor, executor),
    )

    with pytest.raises(CodexCliProviderError) as error:
        await runner.invoke(
            prompt="Prompt",
            output_schema=CoordinatorDecision.model_json_schema(),
            model="gpt-5.4",
            retry_count=0,
        )

    assert error.value.failure is expected


async def test_actual_executor_enforces_timeout_and_output_limit() -> None:
    executor = AsyncioCodexProcessExecutor()
    environment = {"PATH": os.environ.get("PATH", "")}

    with pytest.raises(CodexCliProviderError) as timeout_error:
        await executor.execute(
            [sys.executable, "-c", "import time; time.sleep(1)"],
            stdin=None,
            environment=environment,
            timeout_seconds=0.01,
            output_limit=1_024,
        )
    assert timeout_error.value.failure is CodexCliFailure.TIMEOUT

    with pytest.raises(CodexCliProviderError) as output_error:
        await executor.execute(
            [sys.executable, "-c", "print('x' * 2048)"],
            stdin=None,
            environment=environment,
            timeout_seconds=2,
            output_limit=1_024,
        )
    assert output_error.value.failure is CodexCliFailure.MALFORMED_OUTPUT


async def test_actual_executor_handles_missing_binary_and_cancellation() -> None:
    executor = AsyncioCodexProcessExecutor()
    environment = {"PATH": os.environ.get("PATH", "")}

    with pytest.raises(CodexCliProviderError) as missing_error:
        await executor.execute(
            ["personal-agent-codex-does-not-exist"],
            stdin=None,
            environment=environment,
            timeout_seconds=1,
            output_limit=1_024,
        )
    assert missing_error.value.failure is CodexCliFailure.MISSING_EXECUTABLE

    task = asyncio.create_task(
        executor.execute(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdin=None,
            environment=environment,
            timeout_seconds=10,
            output_limit=1_024,
        )
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_runner_rejects_malformed_jsonl(tmp_path: Path) -> None:
    executor = FakeExecutor(
        [
            *healthy_responses(),
            (process_result(stdout="not-json"), {"message": "Unsafe", "action": None}),
        ]
    )
    runner = CodexCliRunner(
        CodexSubscriptionSettings(enabled=True, working_directory=tmp_path),
        executor=cast(CodexProcessExecutor, executor),
    )

    with pytest.raises(CodexCliProviderError) as error:
        await runner.invoke(
            prompt="Prompt",
            output_schema=CoordinatorDecision.model_json_schema(),
            model="gpt-5.4",
            retry_count=0,
        )

    assert error.value.failure is CodexCliFailure.MALFORMED_OUTPUT


class FakeRunner:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, Any]] = []

    async def health_check(self) -> str:
        return "codex-cli 0.144.0-alpha.4"

    async def invoke(self, **arguments: Any) -> dict[str, Any]:
        self.calls.append(arguments)
        return self.payloads.pop(0)


async def test_subscription_coordinator_validates_and_corrects_output() -> None:
    runner = FakeRunner([{}, {"message": "Corrected", "action": None}])
    coordinator = CodexSubscriptionCoordinator(
        runner=cast(CodexCliRunner, runner),
        settings=CodexSubscriptionSettings(enabled=True, corrective_retries=1),
        model="gpt-5.4",
    )

    decision = await coordinator.decide("Hello")

    assert decision.message == "Corrected"
    assert len(runner.calls) == 2
    assert runner.calls[1]["retry_count"] == 1
    assert "previous response failed" in runner.calls[1]["prompt"]


async def test_subscription_coordinator_composes_typed_evidence() -> None:
    runner = FakeRunner([{"answer": "Grounded", "citations": ["source-1"]}])
    coordinator = CodexSubscriptionCoordinator(
        runner=cast(CodexCliRunner, runner),
        settings=CodexSubscriptionSettings(enabled=True),
        model="gpt-5.4",
    )

    response = await coordinator.compose("Question", [{"identifier": "source-1"}])

    assert response == GroundedResponse(answer="Grounded", citations=["source-1"])
    assert "untrusted data" in runner.calls[0]["prompt"]


class StaticCoordinator:
    def __init__(
        self,
        *,
        decision: CoordinatorDecision | None = None,
        error: Exception | None = None,
    ) -> None:
        self.decision = decision
        self.error = error
        self.calls = 0

    async def decide(self, user_input: str) -> CoordinatorDecision:
        del user_input
        self.calls += 1
        if self.error:
            raise self.error
        assert self.decision is not None
        return self.decision

    async def compose(
        self,
        user_input: str,
        evidence: list[dict[str, Any]],
    ) -> GroundedResponse:
        del user_input, evidence
        raise AssertionError("not used")


async def test_fallback_coordinator_uses_next_provider_only_for_provider_failure() -> None:
    primary = StaticCoordinator(
        error=CodexCliProviderError(CodexCliFailure.RATE_LIMITED)
    )
    fallback = StaticCoordinator(decision=CoordinatorDecision(message="Fallback"))
    coordinator = FallbackCoordinator(
        [("codex-subscription", primary), ("openai", fallback)]
    )

    decision = await coordinator.decide("Hello")

    assert decision.message == "Fallback"
    assert primary.calls == fallback.calls == 1


async def test_fallback_coordinator_does_not_hide_non_provider_error() -> None:
    primary = StaticCoordinator(error=ValueError("invalid user request"))
    fallback = StaticCoordinator(decision=CoordinatorDecision(message="Fallback"))
    coordinator = FallbackCoordinator(
        [("codex-subscription", primary), ("openai", fallback)]
    )

    with pytest.raises(ValueError, match="invalid user request"):
        await coordinator.decide("Hello")

    assert fallback.calls == 0


async def test_fallback_coordinator_handles_retryable_model_http_error() -> None:
    primary = StaticCoordinator(error=ModelHTTPError(429, "primary", {"error": "limited"}))
    fallback = StaticCoordinator(decision=CoordinatorDecision(message="Fallback"))

    decision = await FallbackCoordinator(
        [("openai", primary), ("codex-subscription", fallback)]
    ).decide("Hello")

    assert decision.message == "Fallback"
