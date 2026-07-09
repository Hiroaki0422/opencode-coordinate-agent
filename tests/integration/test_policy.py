"""Integration tests for deterministic authorization and approval lifecycle."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from personal_agent.core.config import PolicySettings
from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.persistence import Database
from personal_agent.persistence.models import ApprovalGrantModel, ApprovalRequestModel, utc_now
from personal_agent.policy import ApprovalExpiredError, PolicyDecision, PolicyService


async def create_session(database: Database, *, expired: bool = False) -> UUID:
    session_id = uuid4()
    expiry_delta = timedelta(minutes=-1 if expired else 120)
    async with database.unit_of_work() as unit_of_work:
        await unit_of_work.sessions.create(
            session_id=session_id,
            expires_at=utc_now() + expiry_delta,
        )
        await unit_of_work.audit.append(
            event_type="session.created",
            actor="test",
            session_id=session_id,
        )
        await unit_of_work.commit()
    return session_id


def action(*, risk_level: RiskLevel, resource: str = "/workspace/note.md") -> ActionRequest:
    return ActionRequest(
        tool_name="filesystem",
        operation="write" if risk_level is not RiskLevel.READ else "read",
        resource=resource,
        risk_level=risk_level,
        summary=f"{risk_level.value} a file",
    )


async def test_reads_are_allowed_and_expired_sessions_are_denied(database: Database) -> None:
    service = PolicyService(database, PolicySettings())
    active_session = await create_session(database)
    expired_session = await create_session(database, expired=True)

    allowed = await service.authorize(
        session_id=active_session,
        action=action(risk_level=RiskLevel.READ),
    )
    denied = await service.authorize(
        session_id=expired_session,
        action=action(risk_level=RiskLevel.READ),
    )

    assert allowed.decision is PolicyDecision.ALLOW
    assert denied.decision is PolicyDecision.DENY
    assert "expired" in denied.reason


async def test_write_approval_creates_a_reusable_session_grant(database: Database) -> None:
    service = PolicyService(database, PolicySettings())
    session_id = await create_session(database)
    requested_action = action(risk_level=RiskLevel.WRITE)

    first = await service.authorize(session_id=session_id, action=requested_action)
    duplicate = await service.authorize(session_id=session_id, action=requested_action)

    assert first.decision is PolicyDecision.REQUIRE_APPROVAL
    assert duplicate.approval_request_id == first.approval_request_id
    assert first.approval_request_id is not None

    await service.approve(first.approval_request_id)
    allowed = await service.authorize(
        session_id=session_id,
        action=action(risk_level=RiskLevel.WRITE),
    )

    assert allowed.decision is PolicyDecision.ALLOW


async def test_risky_approval_does_not_create_a_reusable_grant(database: Database) -> None:
    service = PolicyService(database, PolicySettings())
    session_id = await create_session(database)

    first = await service.authorize(
        session_id=session_id,
        action=action(risk_level=RiskLevel.RISKY),
    )
    assert first.approval_request_id is not None
    await service.approve(first.approval_request_id)

    second = await service.authorize(
        session_id=session_id,
        action=action(risk_level=RiskLevel.RISKY),
    )

    assert second.decision is PolicyDecision.REQUIRE_APPROVAL
    assert second.approval_request_id != first.approval_request_id


async def test_revoked_write_grant_requires_approval_again(database: Database) -> None:
    service = PolicyService(database, PolicySettings())
    session_id = await create_session(database)
    requested = await service.authorize(
        session_id=session_id,
        action=action(risk_level=RiskLevel.WRITE),
    )
    assert requested.approval_request_id is not None
    await service.approve(requested.approval_request_id)

    async with database.engine.connect() as connection:
        grant_id = await connection.scalar(select(ApprovalGrantModel.id))
    assert grant_id is not None
    await service.revoke(UUID(grant_id))

    result = await service.authorize(
        session_id=session_id,
        action=action(risk_level=RiskLevel.WRITE),
    )
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL


async def test_expired_approval_is_denied_and_audited(database: Database) -> None:
    service = PolicyService(database, PolicySettings(approval_ttl_minutes=1))
    session_id = await create_session(database)
    result = await service.authorize(
        session_id=session_id,
        action=action(risk_level=RiskLevel.WRITE),
    )
    assert result.approval_request_id is not None

    async with database.engine.begin() as connection:
        await connection.execute(
            update(ApprovalRequestModel)
            .where(ApprovalRequestModel.id == str(result.approval_request_id))
            .values(created_at=utc_now() - timedelta(minutes=2))
        )

    with pytest.raises(ApprovalExpiredError):
        await service.approve(result.approval_request_id)

    async with database.unit_of_work() as unit_of_work:
        stored = await unit_of_work.approvals.get_request(result.approval_request_id)
        assert stored is not None
        stored_status = stored.status

    assert stored_status == "denied"
