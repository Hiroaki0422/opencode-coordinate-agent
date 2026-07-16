"""Offline behavior tests for the authenticated Telegram transport."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from personal_agent.application import (
    AgentRunResult,
    ConversationMessage,
    OperationReceiptInspection,
    SessionInspection,
    TelegramActionAuthorization,
)
from personal_agent.core.config import TelegramSettings
from personal_agent.telegram.bot import TelegramBot
from personal_agent.telegram.client import TelegramMessage, TelegramUpdate


class FakeState:
    def __init__(self) -> None:
        self.session_id = uuid4()
        self.claimed: set[int] = set()
        self.rejections: list[tuple[int | None, int | None]] = []
        self.created_tokens: list[tuple[UUID, UUID, int, int]] = []
        self.consumed: list[tuple[str, int, int, bool]] = []

    async def claim_update(self, update_id: int) -> bool:
        if update_id in self.claimed:
            return False
        self.claimed.add(update_id)
        return True

    async def get_or_create_session(
        self,
        *,
        chat_id: int,
        user_id: int,
        force_new: bool = False,
    ) -> UUID:
        del chat_id, user_id
        if force_new:
            self.session_id = uuid4()
        return self.session_id

    async def create_action_token(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        chat_id: int,
        user_id: int,
        expires_at: datetime,
    ) -> str:
        del expires_at
        self.created_tokens.append((session_id, run_id, chat_id, user_id))
        return "one-time-token"

    async def consume_action_token(
        self,
        token: str,
        *,
        chat_id: int,
        user_id: int,
        approved: bool,
    ) -> TelegramActionAuthorization:
        self.consumed.append((token, chat_id, user_id, approved))
        return TelegramActionAuthorization(
            session_id=self.session_id,
            run_id=RUN_ID,
            approved=approved,
        )

    async def audit_rejection(
        self,
        *,
        chat_id: int | None,
        user_id: int | None,
        reason: str,
    ) -> None:
        del reason
        self.rejections.append((chat_id, user_id))


class FakeRuntime:
    def __init__(self, results: list[AgentRunResult]) -> None:
        self.telegram = FakeState()
        self.results = results
        self.submit_calls: list[tuple[str, UUID | None]] = []
        self.resume_calls: list[tuple[UUID, bool]] = []
        self.active = "/workspaces/todo-test"
        self.available = ("/workspaces/todo-test", "/workspaces/other")
        self.receipt = OperationReceiptInspection(
            run_id=RUN_ID,
            action_id=uuid4(),
            tool_name="opencode",
            operation="code_task",
            resource=self.active,
            success=False,
            outcome="partial",
            created_at=datetime.now(UTC),
            payload={
                "changed_files": ["todo.py"],
                "verification_reason": "expected_files_missing",
                "worker_events": [
                    {"sequence": 1, "type": "text", "text": "Created todo.py"}
                ],
            },
        )

    async def submit(self, prompt: str, *, session_id: UUID | None = None) -> AgentRunResult:
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
        return ()

    async def clear_conversation_history(self, session_id: UUID) -> int:
        del session_id
        return 0

    async def active_workspace(self, session_id: UUID) -> str | None:
        del session_id
        return self.active

    async def select_workspace(self, session_id: UUID, resource: str) -> str:
        del session_id
        self.active = f"/workspaces/{resource}"
        return self.active

    async def list_workspaces(self, session_id: UUID) -> tuple[str, ...]:
        del session_id
        return self.available

    async def operation_receipt(
        self,
        session_id: UUID,
        run_id: UUID | None = None,
    ) -> OperationReceiptInspection | None:
        del session_id
        return self.receipt if run_id in {None, RUN_ID} else None


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.edited: list[dict[str, object]] = []
        self.answered: list[dict[str, object]] = []
        self.actions: list[int] = []
        self.next_message_id = 100

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        self.next_message_id += 1
        return TelegramMessage.model_validate(
            {
                "message_id": self.next_message_id,
                "chat": {"id": kwargs["chat_id"], "type": "private"},
                "text": kwargs["text"],
            }
        )

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)
        return TelegramMessage.model_validate(
            {
                "message_id": kwargs["message_id"],
                "chat": {"id": kwargs["chat_id"], "type": "private"},
                "text": kwargs["text"],
            }
        )

    async def answer_callback_query(self, **kwargs) -> None:
        self.answered.append(kwargs)

    async def send_chat_action(self, *, chat_id: int, action: str = "typing") -> None:
        del action
        self.actions.append(chat_id)


RUN_ID = uuid4()
SESSION_ID = uuid4()


def completed(response: str) -> AgentRunResult:
    return AgentRunResult(
        session_id=SESSION_ID,
        run_id=RUN_ID,
        status="completed",
        response=response,
        interrupts=(),
    )


def settings() -> TelegramSettings:
    return TelegramSettings(allowed_chat_ids=[11], allowed_user_ids=[22])


def message_update(*, update_id: int, chat_id: int = 11, user_id: int = 22, text: str):
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "from": {"id": user_id, "is_bot": False},
                "chat": {"id": chat_id, "type": "private"},
                "text": text,
            },
        }
    )


async def test_allowed_message_uses_bound_session_and_replay_is_ignored() -> None:
    runtime = FakeRuntime([completed("answer")])
    client = FakeClient()
    bot = TelegramBot(settings=settings(), runtime=runtime, client=client)
    update = message_update(update_id=1, text="hello")

    await bot.process_update(update)
    await bot.process_update(update)

    assert runtime.submit_calls == [("hello", runtime.telegram.session_id)]
    assert client.sent[0]["text"] == "Planning…"
    assert client.edited[-1]["text"] == "answer"


async def test_message_requires_both_chat_and_user_allowlists() -> None:
    runtime = FakeRuntime([])
    client = FakeClient()
    bot = TelegramBot(settings=settings(), runtime=runtime, client=client)

    await bot.process_update(message_update(update_id=2, user_id=999, text="hello"))

    assert runtime.submit_calls == []
    assert runtime.telegram.rejections == [(11, 999)]
    assert client.sent == []


async def test_approval_renders_details_and_callback_resumes_once() -> None:
    paused = AgentRunResult(
        session_id=SESSION_ID,
        run_id=RUN_ID,
        status="planned",
        response=None,
        interrupts=(
            {
                "tool_name": "local_execution",
                "operation": "run_command",
                "resource": "/workspace/project",
                "summary": "Run pytest",
                "risk_level": "risky",
                "reason": "Executes a process",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            },
        ),
    )
    runtime = FakeRuntime([paused, completed("Tests passed")])
    client = FakeClient()
    bot = TelegramBot(settings=settings(), runtime=runtime, client=client)

    await bot.process_update(message_update(update_id=3, text="run tests"))
    approval_edit = client.edited[-1]
    markup = approval_edit["reply_markup"]
    approve_data = markup["inline_keyboard"][0][0]["callback_data"]
    assert "Tool: local_execution" in approval_edit["text"]
    assert len(approve_data.encode()) <= 64

    callback = TelegramUpdate.model_validate(
        {
            "update_id": 4,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 22, "is_bot": False},
                "message": {
                    "message_id": 101,
                    "chat": {"id": 11, "type": "private"},
                    "text": "Approval required",
                },
                "data": approve_data,
            },
        }
    )
    await bot.process_update(callback)

    assert runtime.telegram.consumed == [("one-time-token", 11, 22, True)]
    assert runtime.resume_calls == [(RUN_ID, True)]
    assert client.answered[-1]["text"] == "Approved"
    assert client.edited[-1]["text"] == "Tests passed"


async def test_workspace_and_operation_commands_are_session_scoped() -> None:
    runtime = FakeRuntime([])
    client = FakeClient()
    bot = TelegramBot(settings=settings(), runtime=runtime, client=client)

    await bot.process_update(message_update(update_id=20, text="/workspace"))
    await bot.process_update(message_update(update_id=21, text="/workspace new-project"))
    await bot.process_update(message_update(update_id=22, text="/workspaces"))
    await bot.process_update(message_update(update_id=23, text="/last-operation"))
    await bot.process_update(
        message_update(update_id=24, text=f"/operation {RUN_ID} log")
    )

    sent = [str(item["text"]) for item in client.sent]
    assert "Active workspace: /workspaces/todo-test" in sent
    assert "Active workspace: /workspaces/new-project" in sent
    assert any("/workspaces/other" in item for item in sent)
    assert any("Changed files: todo.py" in item for item in sent)
    assert any("Created todo.py" in item for item in sent)


async def test_nonexistent_workspace_selection_returns_deterministic_error() -> None:
    runtime = FakeRuntime([])

    async def reject(session_id: UUID, resource: str) -> str:
        del session_id, resource
        raise RuntimeError("workspace does not exist")

    runtime.select_workspace = reject  # type: ignore[method-assign]
    client = FakeClient()
    bot = TelegramBot(settings=settings(), runtime=runtime, client=client)

    await bot.process_update(message_update(update_id=27, text="/workspace missing"))

    assert client.sent[-1]["text"] == (
        "Workspace selection failed: workspace does not exist"
    )


async def test_natural_opencode_log_request_bypasses_model_inference() -> None:
    runtime = FakeRuntime([])
    client = FakeClient()
    bot = TelegramBot(settings=settings(), runtime=runtime, client=client)

    await bot.process_update(
        message_update(update_id=25, text="Show the OpenCode operation log")
    )

    assert runtime.submit_calls == []
    assert client.sent[-1]["text"] == "#1 text: Created todo.py"


async def test_created_file_followup_resolves_single_receipt_path() -> None:
    runtime = FakeRuntime([completed("contents")])
    client = FakeClient()
    bot = TelegramBot(settings=settings(), runtime=runtime, client=client)

    await bot.process_update(
        message_update(update_id=26, text="Show me the file OpenCode created")
    )

    assert runtime.submit_calls == [
        (
            "Read the file 'todo.py' from the current workspace and show its contents.",
            runtime.telegram.session_id,
        )
    ]
    assert client.edited[-1]["text"] == "contents"
