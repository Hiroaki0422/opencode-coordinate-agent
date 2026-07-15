"""Integration tests for SQLite migrations and audited repositories."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DatabaseError
from sqlalchemy.sql.dml import Delete, Update

from personal_agent.core.types import ActionRequest, ApprovalGrant, RiskLevel
from personal_agent.persistence import (
    MAX_CONVERSATION_MESSAGE_CHARS,
    ConversationMessageRole,
    Database,
    MissingAuditEventError,
)
from personal_agent.persistence.models import (
    ApprovalGrantModel,
    ApprovalRequestModel,
    AuditEventModel,
    ConversationMessageModel,
    SessionModel,
    WorkflowRunModel,
    utc_now,
)


async def test_migrations_are_idempotent(database: Database) -> None:
    await database.initialize()

    async with database.engine.connect() as connection:
        result = await connection.execute(
            text("SELECT version, description FROM schema_migrations ORDER BY version")
        )

    applied_migrations = [tuple(row) for row in result]
    assert applied_migrations == [
        (1, "initial persistence schema"),
        (2, "conversation message storage"),
        (3, "telegram transport state"),
    ]


async def test_state_change_and_audit_event_commit_atomically(database: Database) -> None:
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
            payload={"source": "integration-test"},
        )
        await unit_of_work.commit()

    async with database.engine.connect() as connection:
        session_count = await connection.scalar(
            select(func.count()).select_from(SessionModel)
        )
        audit_count = await connection.scalar(
            select(func.count()).select_from(AuditEventModel)
        )

    assert session_count == 1
    assert audit_count == 1


async def test_state_change_without_audit_event_rolls_back(database: Database) -> None:
    session_id = uuid4()

    with pytest.raises(MissingAuditEventError):
        async with database.unit_of_work() as unit_of_work:
            await unit_of_work.sessions.create(
                session_id=session_id,
                expires_at=utc_now() + timedelta(hours=2),
            )
            await unit_of_work.commit()

    async with database.unit_of_work() as unit_of_work:
        stored_session = await unit_of_work.sessions.get(session_id)

    assert stored_session is None


async def test_approval_and_workflow_repositories_persist_records(database: Database) -> None:
    session_id = uuid4()
    request_id = uuid4()
    run_id = uuid4()
    action = ActionRequest(
        tool_name="filesystem",
        operation="write",
        resource="/workspace/notes.md",
        risk_level=RiskLevel.WRITE,
        summary="Update a workspace note",
    )
    grant = ApprovalGrant(
        session_id=session_id,
        tool_name=action.tool_name,
        operation=action.operation,
        resource_pattern=action.resource,
        risk_level=action.risk_level,
        expires_at=utc_now() + timedelta(minutes=15),
    )

    async with database.unit_of_work() as unit_of_work:
        await unit_of_work.sessions.create(
            session_id=session_id,
            expires_at=utc_now() + timedelta(hours=2),
        )
        await unit_of_work.approvals.create_request(
            session_id=session_id,
            action=action,
            request_id=request_id,
        )
        await unit_of_work.approvals.create_grant(
            grant,
            approval_request_id=request_id,
        )
        await unit_of_work.workflow_runs.create(
            session_id=session_id,
            input_summary="Write a note",
            run_id=run_id,
        )
        await unit_of_work.audit.append(
            event_type="workflow.prepared",
            actor="test",
            session_id=session_id,
            run_id=run_id,
            approval_id=request_id,
        )
        await unit_of_work.commit()

    async with database.engine.connect() as connection:
        request_count = await connection.scalar(
            select(func.count()).select_from(ApprovalRequestModel)
        )
        grant_count = await connection.scalar(
            select(func.count()).select_from(ApprovalGrantModel)
        )
        run_count = await connection.scalar(
            select(func.count()).select_from(WorkflowRunModel)
        )

    assert request_count == 1
    assert grant_count == 1
    assert run_count == 1


async def test_conversation_repository_persists_ordered_bounded_messages(
    database: Database,
) -> None:
    session_id = uuid4()
    run_id = uuid4()

    async with database.unit_of_work() as unit_of_work:
        await unit_of_work.sessions.create(
            session_id=session_id,
            expires_at=utc_now() + timedelta(hours=2),
        )
        await unit_of_work.workflow_runs.create(session_id=session_id, run_id=run_id)
        await unit_of_work.conversations.create(
            session_id=session_id,
            run_id=run_id,
            role=ConversationMessageRole.USER,
            content="Update app.py",
        )
        await unit_of_work.conversations.create(
            session_id=session_id,
            run_id=run_id,
            role=ConversationMessageRole.ASSISTANT,
            content="Updated app.py",
        )
        await unit_of_work.audit.append(
            event_type="conversation.messages_created",
            actor="test",
            session_id=session_id,
            run_id=run_id,
        )
        await unit_of_work.commit()

    async with database.unit_of_work() as unit_of_work:
        messages = await unit_of_work.conversations.list_for_session(session_id)
        latest = await unit_of_work.conversations.list_for_session(session_id, limit=1)
        stored_messages = [(item.role, item.content) for item in messages]
        latest_content = [item.content for item in latest]

    assert stored_messages == [
        (ConversationMessageRole.USER.value, "Update app.py"),
        (ConversationMessageRole.ASSISTANT.value, "Updated app.py"),
    ]
    assert latest_content == ["Updated app.py"]


async def test_conversation_repository_rejects_invalid_content(database: Database) -> None:
    session_id = uuid4()
    run_id = uuid4()

    async with database.unit_of_work() as unit_of_work:
        with pytest.raises(ValueError, match="cannot be empty"):
            await unit_of_work.conversations.create(
                session_id=session_id,
                run_id=run_id,
                role=ConversationMessageRole.USER,
                content="   ",
            )
        with pytest.raises(ValueError, match="exceeds"):
            await unit_of_work.conversations.create(
                session_id=session_id,
                run_id=run_id,
                role=ConversationMessageRole.USER,
                content="x" * (MAX_CONVERSATION_MESSAGE_CHARS + 1),
            )


async def test_conversation_messages_cascade_with_session(database: Database) -> None:
    session_id = uuid4()
    run_id = uuid4()

    async with database.unit_of_work() as unit_of_work:
        await unit_of_work.sessions.create(
            session_id=session_id,
            expires_at=utc_now() + timedelta(hours=2),
        )
        await unit_of_work.workflow_runs.create(session_id=session_id, run_id=run_id)
        await unit_of_work.conversations.create(
            session_id=session_id,
            run_id=run_id,
            role=ConversationMessageRole.USER,
            content="Temporary message",
        )
        await unit_of_work.audit.append(
            event_type="conversation.message_created",
            actor="test",
            session_id=session_id,
            run_id=run_id,
        )
        await unit_of_work.commit()

    async with database.engine.begin() as connection:
        await connection.execute(
            delete(SessionModel).where(SessionModel.id == str(session_id))
        )

    async with database.engine.connect() as connection:
        message_count = await connection.scalar(
            select(func.count()).select_from(ConversationMessageModel)
        )

    assert message_count == 0


@pytest.mark.parametrize("statement_factory", [update, delete])
async def test_audit_events_reject_updates_and_deletes(
    database: Database,
    statement_factory: object,
) -> None:
    event_id = uuid4()

    async with database.unit_of_work() as unit_of_work:
        await unit_of_work.audit.append(
            event_id=event_id,
            event_type="test.event",
            actor="test",
            payload={"immutable": True},
        )
        await unit_of_work.commit()

    statement: Update | Delete
    if statement_factory is update:
        statement = update(AuditEventModel).where(AuditEventModel.id == str(event_id)).values(
            actor="modified"
        )
    else:
        statement = delete(AuditEventModel).where(AuditEventModel.id == str(event_id))

    with pytest.raises(DatabaseError, match="append-only"):
        async with database.engine.begin() as connection:
            await connection.execute(statement)

    async with database.engine.connect() as connection:
        stored_event_id = await connection.scalar(
            select(AuditEventModel.id).where(AuditEventModel.id == str(event_id))
        )

    assert stored_event_id is not None
    assert UUID(stored_event_id) == event_id
