"""Integration tests for transport-neutral runtime operations."""

import asyncio
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import select

from personal_agent.application import AgentRuntime
from personal_agent.core.config import ConversationSettings, Settings
from personal_agent.graph import CompiledAgentGraph
from personal_agent.persistence import (
    MAX_CONVERSATION_MESSAGE_CHARS,
    Database,
    RecordNotFoundError,
)
from personal_agent.persistence.models import (
    ConversationMessageModel,
    WorkflowRunModel,
    WorkflowRunStatus,
)


class FakeGraph:
    def __init__(self, results: list[dict[str, Any] | BaseException]) -> None:
        self._results = results
        self.inputs: list[Any] = []

    async def ainvoke(self, input_value: Any, *, config: Any) -> dict[str, Any]:
        del config
        self.inputs.append(input_value)
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@dataclass(frozen=True)
class FakeInterrupt:
    value: dict[str, Any]


def build_runtime(
    database: Database,
    graph: FakeGraph,
    *,
    conversation: ConversationSettings | None = None,
) -> AgentRuntime:
    settings = Settings.model_validate(
        {"conversation": (conversation or ConversationSettings()).model_dump()}
    )
    return AgentRuntime(
        settings=settings,
        database=database,
        graph=cast(CompiledAgentGraph, graph),
        actor="test-runtime",
    )


async def test_runtime_persists_user_and_terminal_assistant_messages(
    database: Database,
) -> None:
    graph = FakeGraph(
        [{"status": "completed", "response": "The update is complete."}]
    )
    runtime = build_runtime(database, graph)

    result = await runtime.submit("Update app.py")

    async with database.unit_of_work() as unit_of_work:
        messages = await unit_of_work.conversations.list_for_session(result.session_id)
        run = await unit_of_work.workflow_runs.get(result.run_id)
        stored_messages = [(message.role, message.content) for message in messages]
        run_status = run.status if run is not None else None

    assert result.as_dict()["status"] == "completed"
    assert stored_messages == [
        ("user", "Update app.py"),
        ("assistant", "The update is complete."),
    ]
    assert run_status == WorkflowRunStatus.SUCCEEDED.value


async def test_runtime_pauses_then_records_assistant_after_resume(
    database: Database,
) -> None:
    graph = FakeGraph(
        [
            {
                "status": "planned",
                "__interrupt__": [
                    FakeInterrupt(
                        {
                            "approval_request_id": str(uuid4()),
                            "summary": "Update app.py",
                            "risk_level": "write",
                        }
                    )
                ],
            },
            {"status": "completed", "response": "Approved update completed."},
        ]
    )
    runtime = build_runtime(database, graph)

    paused = await runtime.submit("Update app.py")
    inspection = await runtime.inspect(paused.run_id)
    async with database.unit_of_work() as unit_of_work:
        paused_messages = await unit_of_work.conversations.list_for_session(
            paused.session_id
        )
        paused_roles = [message.role for message in paused_messages]

    assert paused.as_dict()["status"] == "approval_required"
    assert inspection.status == WorkflowRunStatus.PAUSED.value
    assert inspection.current_node == "approval"
    assert paused_roles == ["user"]

    resumed = await runtime.resume(paused.run_id, approved=True)
    async with database.unit_of_work() as unit_of_work:
        completed_messages = await unit_of_work.conversations.list_for_session(
            paused.session_id
        )
        completed_roles = [message.role for message in completed_messages]

    assert resumed.response == "Approved update completed."
    assert completed_roles == ["user", "assistant"]


async def test_runtime_rejects_unknown_session(database: Database) -> None:
    runtime = build_runtime(database, FakeGraph([]))

    with pytest.raises(RecordNotFoundError, match="session"):
        await runtime.submit("Hello", session_id=uuid4())


async def test_runtime_supplies_only_complete_bounded_prior_turns(
    database: Database,
) -> None:
    graph = FakeGraph(
        [
            {"status": "completed", "response": "First answer"},
            {"status": "completed", "response": "Second answer"},
            {"status": "completed", "response": "Third answer"},
        ]
    )
    runtime = build_runtime(
        database,
        graph,
        conversation=ConversationSettings(max_turns=1, max_context_chars=1_000),
    )

    first = await runtime.submit("First question")
    await runtime.submit("Second question", session_id=first.session_id)
    await runtime.submit("Refer to that", session_id=first.session_id)

    assert graph.inputs[0]["conversation_history"] == []
    assert graph.inputs[1]["conversation_history"] == [
        {"user": "First question", "assistant": "First answer"}
    ]
    assert graph.inputs[2]["conversation_history"] == [
        {"user": "Second question", "assistant": "Second answer"}
    ]


async def test_runtime_omits_turns_exceeding_context_character_limit(
    database: Database,
) -> None:
    graph = FakeGraph(
        [
            {"status": "completed", "response": "a" * 600},
            {"status": "completed", "response": "No history used"},
        ]
    )
    runtime = build_runtime(
        database,
        graph,
        conversation=ConversationSettings(max_turns=20, max_context_chars=1_000),
    )

    first = await runtime.submit("u" * 600)
    await runtime.submit("Follow up", session_id=first.session_id)

    assert graph.inputs[1]["conversation_history"] == []


async def test_runtime_context_survives_restart_and_excludes_paused_turn(
    database: Database,
) -> None:
    first_graph = FakeGraph(
        [{"status": "completed", "response": "Remembered answer"}]
    )
    first_runtime = build_runtime(database, first_graph)
    first = await first_runtime.submit("Remember this")

    restarted_graph = FakeGraph(
        [
            {
                "status": "planned",
                "__interrupt__": [FakeInterrupt({"summary": "Needs approval"})],
            },
            {"status": "completed", "response": "Used prior context"},
        ]
    )
    restarted_runtime = build_runtime(database, restarted_graph)
    await restarted_runtime.submit("Incomplete turn", session_id=first.session_id)
    await restarted_runtime.submit("What did I say?", session_id=first.session_id)

    expected_history = [
        {"user": "Remember this", "assistant": "Remembered answer"}
    ]
    assert restarted_graph.inputs[0]["conversation_history"] == expected_history
    assert restarted_graph.inputs[1]["conversation_history"] == expected_history


async def test_runtime_clear_history_is_independent_and_audited(
    database: Database,
) -> None:
    runtime = build_runtime(
        database,
        FakeGraph([{"status": "completed", "response": "Stored answer"}]),
    )
    result = await runtime.submit("Stored question")

    deleted = await runtime.clear_conversation_history(result.session_id)
    history = await runtime.conversation_history(result.session_id)
    status = await runtime.session_status(result.session_id)

    assert deleted == 2
    assert history == ()
    assert status.latest_run_id == result.run_id


async def test_runtime_marks_cancelled_turn_without_assistant_message(
    database: Database,
) -> None:
    runtime = build_runtime(database, FakeGraph([asyncio.CancelledError()]))

    with pytest.raises(asyncio.CancelledError):
        await runtime.submit("Cancel this")

    async with database.engine.connect() as connection:
        run_status = await connection.scalar(select(WorkflowRunModel.status))
        roles = list(
            (
                await connection.execute(
                    select(ConversationMessageModel.role).order_by(
                        ConversationMessageModel.sequence
                    )
                )
            ).scalars()
        )

    assert run_status == WorkflowRunStatus.CANCELLED.value
    assert roles == ["user"]


async def test_runtime_redacts_configured_secrets_before_sqlite_persistence(
    database: Database,
) -> None:
    secret = "configured-openai-secret"
    settings = Settings.model_validate(
        {"openai": {"enabled": True, "api_key": secret}}
    )
    graph = FakeGraph(
        [
            {
                "status": "completed",
                "response": f"I did not store bearer token: Bearer {secret}",
            }
        ]
    )
    runtime = AgentRuntime(
        settings=settings,
        database=database,
        graph=cast(CompiledAgentGraph, graph),
        actor="test-runtime",
    )

    await runtime.submit(f"Never persist {secret}")

    async with database.engine.connect() as connection:
        contents = list(
            (await connection.execute(select(ConversationMessageModel.content))).scalars()
        )
        summaries = list(
            (await connection.execute(select(WorkflowRunModel.input_summary))).scalars()
        )

    assert all(secret not in content for content in contents)
    assert all(secret not in summary for summary in summaries if summary is not None)
    assert all("[REDACTED]" in content for content in contents)


async def test_runtime_truncates_oversized_assistant_history_message(
    database: Database,
) -> None:
    runtime = build_runtime(
        database,
        FakeGraph(
            [
                {
                    "status": "completed",
                    "response": "x" * (MAX_CONVERSATION_MESSAGE_CHARS + 100),
                }
            ]
        ),
    )

    result = await runtime.submit("Generate a long response")
    history = await runtime.conversation_history(result.session_id)

    assert len(history[-1].content) == MAX_CONVERSATION_MESSAGE_CHARS
    assert history[-1].content.endswith("[conversation message truncated]")
