"""Integration tests for durable LangGraph approval interrupts."""

from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from langgraph.types import Command

from personal_agent.core.config import PolicySettings
from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.graph import AgentState, open_agent_graph
from personal_agent.models import CoordinatorDecision
from personal_agent.persistence import Database
from personal_agent.persistence.models import utc_now
from personal_agent.policy import PolicyService


class FakeCoordinator:
    def __init__(self, decision: CoordinatorDecision) -> None:
        self.decision = decision
        self.calls = 0

    async def decide(self, user_input: str) -> CoordinatorDecision:
        del user_input
        self.calls += 1
        return self.decision


async def create_session(database: Database) -> str:
    session_id = uuid4()
    async with database.unit_of_work() as unit_of_work:
        await unit_of_work.sessions.create(
            session_id=session_id,
            expires_at=utc_now() + timedelta(hours=2),
        )
        await unit_of_work.audit.append(
            event_type="session.created",
            actor="test",
            session_id=session_id,
        )
        await unit_of_work.commit()
    return str(session_id)


async def test_graph_resumes_approval_from_durable_checkpoint(
    database: Database,
    tmp_path: Path,
) -> None:
    session_id = await create_session(database)
    run_id = str(uuid4())
    checkpoint_path = tmp_path / "checkpoints.sqlite3"
    decision = CoordinatorDecision(
        message="I can update that file.",
        action=ActionRequest(
            tool_name="filesystem",
            operation="write",
            resource="/workspace/note.md",
            risk_level=RiskLevel.WRITE,
            summary="Update a note",
        ),
    )
    initial_coordinator = FakeCoordinator(decision)
    policy = PolicyService(database, PolicySettings())
    config = {"configurable": {"thread_id": run_id}}
    initial_state: AgentState = {
        "session_id": session_id,
        "run_id": run_id,
        "user_input": "Update my note",
    }

    async with open_agent_graph(
        checkpoint_path=checkpoint_path,
        coordinator=initial_coordinator,
        policy=policy,
    ) as graph:
        initial_raw = await graph.ainvoke(initial_state, config=cast(Any, config))
        initial = cast(dict[str, Any], initial_raw)

    assert "__interrupt__" in initial
    assert initial_coordinator.calls == 1

    resume_coordinator = FakeCoordinator(decision)
    async with open_agent_graph(
        checkpoint_path=checkpoint_path,
        coordinator=resume_coordinator,
        policy=policy,
    ) as graph:
        resumed_raw = await graph.ainvoke(Command(resume=True), config=cast(Any, config))
        resumed = cast(dict[str, Any], resumed_raw)

    assert resumed["status"] == "authorized"
    assert resume_coordinator.calls == 0


async def test_graph_records_denied_human_decision(
    database: Database,
    tmp_path: Path,
) -> None:
    session_id = await create_session(database)
    run_id = str(uuid4())
    coordinator = FakeCoordinator(
        CoordinatorDecision(
            message="I can remove that file.",
            action=ActionRequest(
                tool_name="filesystem",
                operation="delete",
                resource="/workspace/note.md",
                risk_level=RiskLevel.RISKY,
                summary="Delete a note",
            ),
        )
    )
    policy = PolicyService(database, PolicySettings())
    config = {"configurable": {"thread_id": run_id}}
    state: AgentState = {
        "session_id": session_id,
        "run_id": run_id,
        "user_input": "Delete my note",
    }

    async with open_agent_graph(
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
        coordinator=coordinator,
        policy=policy,
    ) as graph:
        await graph.ainvoke(state, config=cast(Any, config))
        denied_raw = await graph.ainvoke(Command(resume=False), config=cast(Any, config))
        denied = cast(dict[str, Any], denied_raw)

    assert denied["status"] == "denied"
    assert "human denied" in denied["response"]
