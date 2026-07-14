"""Tests for the interactive terminal transport."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from personal_agent.application import (
    AgentRunResult,
    ConversationMessage,
    SessionInspection,
)
from personal_agent.cli.chat import InteractiveChat


class FakeTerminal:
    def __init__(self, inputs: list[str | BaseException]) -> None:
        self._inputs = inputs
        self.outputs: list[str] = []
        self.prompts: list[str] = []

    async def read(self, prompt: str) -> str:
        self.prompts.append(prompt)
        value = self._inputs.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def write(self, message: str = "") -> None:
        self.outputs.append(message)


class FakeRuntime:
    def __init__(self, results: list[AgentRunResult] | None = None) -> None:
        self.session_ids = [uuid4(), uuid4(), uuid4()]
        self.results = results or []
        self.submit_calls: list[tuple[str, UUID | None]] = []
        self.resume_calls: list[tuple[UUID, bool]] = []
        self.cleared_sessions: list[UUID] = []
        self.history = (
            ConversationMessage(
                id=str(uuid4()),
                run_id=uuid4(),
                role="user",
                content="Earlier question",
                created_at=datetime.now(UTC),
            ),
        )

    async def create_session(self) -> UUID:
        return self.session_ids.pop(0)

    async def submit(
        self,
        prompt: str,
        *,
        session_id: UUID | None = None,
    ) -> AgentRunResult:
        self.submit_calls.append((prompt, session_id))
        return self.results.pop(0)

    async def resume(self, run_id: UUID, *, approved: bool) -> AgentRunResult:
        self.resume_calls.append((run_id, approved))
        return self.results.pop(0)

    async def session_status(self, session_id: UUID) -> SessionInspection:
        return SessionInspection(
            session_id=session_id,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            latest_run_id=None,
            latest_run_status=None,
        )

    async def conversation_history(
        self,
        session_id: UUID,
    ) -> tuple[ConversationMessage, ...]:
        del session_id
        return self.history

    async def clear_conversation_history(self, session_id: UUID) -> int:
        self.cleared_sessions.append(session_id)
        deleted = len(self.history)
        self.history = ()
        return deleted


def completed(response: str) -> AgentRunResult:
    return AgentRunResult(
        session_id=uuid4(),
        run_id=uuid4(),
        status="completed",
        response=response,
        interrupts=(),
    )


async def test_interactive_chat_submits_multiple_turns_in_one_session() -> None:
    runtime = FakeRuntime([completed("First answer"), completed("Second answer")])
    terminal = FakeTerminal(["First question", "Follow up", "/quit"])

    await InteractiveChat(runtime, terminal).run()

    active_session = runtime.submit_calls[0][1]
    assert runtime.submit_calls == [
        ("First question", active_session),
        ("Follow up", active_session),
    ]
    assert "assistant> First answer" in terminal.outputs
    assert "assistant> Second answer" in terminal.outputs


async def test_interactive_chat_requires_explicit_inline_approval() -> None:
    run_id = uuid4()
    paused = AgentRunResult(
        session_id=uuid4(),
        run_id=run_id,
        status="planned",
        response=None,
        interrupts=(
            {
                "tool_name": "opencode",
                "operation": "code_task",
                "resource": "/workspace/project",
                "summary": "Update app.py",
                "risk_level": "risky",
                "expires_at": "2026-07-14T12:00:00+00:00",
            },
        ),
    )
    runtime = FakeRuntime([paused, completed("Changes completed")])
    terminal = FakeTerminal(["Update the app", "looks good", "approve", "/quit"])

    await InteractiveChat(runtime, terminal).run()

    assert runtime.resume_calls == [(run_id, True)]
    assert any("Enter `approve` or `deny`" in output for output in terminal.outputs)
    assert any("Resource: /workspace/project" in output for output in terminal.outputs)


async def test_empty_or_interrupted_approval_denies() -> None:
    run_id = uuid4()
    paused = AgentRunResult(
        session_id=uuid4(),
        run_id=run_id,
        status="planned",
        response=None,
        interrupts=({"summary": "Risky action"},),
    )
    runtime = FakeRuntime([paused, completed("Action denied")])
    terminal = FakeTerminal(["Do risky work", "", "/quit"])

    await InteractiveChat(runtime, terminal).run()

    assert runtime.resume_calls == [(run_id, False)]


async def test_interactive_commands_manage_local_session_history() -> None:
    runtime = FakeRuntime()
    original_session = runtime.session_ids[0]
    terminal = FakeTerminal(
        [
            "/help",
            "/session",
            "/status",
            "/history",
            "/clear",
            "/new",
            "/quit",
        ]
    )

    await InteractiveChat(runtime, terminal).run()

    assert runtime.cleared_sessions == [original_session]
    assert "user> Earlier question" in terminal.outputs
    assert any(output.startswith("New session:") for output in terminal.outputs)


async def test_ctrl_c_cancels_input_and_ctrl_d_exits_cleanly() -> None:
    runtime = FakeRuntime()
    terminal = FakeTerminal([KeyboardInterrupt(), EOFError()])

    await InteractiveChat(runtime, terminal).run()

    assert "Input cancelled. Type /quit to exit." in terminal.outputs
    assert terminal.outputs[-1] == "Exiting."
