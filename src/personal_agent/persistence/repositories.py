"""Repositories and audited transaction boundary for durable state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from personal_agent.core.types import ActionRequest, ApprovalGrant
from personal_agent.persistence.models import (
    ApprovalGrantModel,
    ApprovalRequestModel,
    ApprovalRequestStatus,
    AuditEventModel,
    SessionModel,
    SessionStatus,
    WorkflowRunModel,
    WorkflowRunStatus,
    utc_now,
)


class MissingAuditEventError(RuntimeError):
    """Raised when a state-changing unit of work has no audit event."""


class RecordNotFoundError(LookupError):
    """Raised when a requested persistence record does not exist."""


@dataclass
class _ChangeTracker:
    state_changes: int = 0
    audit_events: int = 0


class SessionRepository:
    def __init__(self, session: AsyncSession, tracker: _ChangeTracker) -> None:
        self._session = session
        self._tracker = tracker

    async def create(
        self, *, expires_at: datetime, session_id: UUID | None = None
    ) -> SessionModel:
        now = utc_now()
        model = SessionModel(
            id=str(session_id or uuid4()),
            status=SessionStatus.ACTIVE.value,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        self._session.add(model)
        await self._session.flush()
        self._tracker.state_changes += 1
        return model

    async def get(self, session_id: UUID) -> SessionModel | None:
        return await self._session.get(SessionModel, str(session_id))

    async def close(self, session_id: UUID) -> SessionModel:
        model = await self.get(session_id)
        if model is None:
            raise RecordNotFoundError(f"session {session_id} was not found")
        model.status = SessionStatus.CLOSED.value
        model.updated_at = utc_now()
        self._tracker.state_changes += 1
        return model


class ApprovalRepository:
    def __init__(self, session: AsyncSession, tracker: _ChangeTracker) -> None:
        self._session = session
        self._tracker = tracker

    async def create_request(
        self,
        *,
        session_id: UUID,
        action: ActionRequest,
        request_id: UUID | None = None,
    ) -> ApprovalRequestModel:
        model = ApprovalRequestModel(
            id=str(request_id or uuid4()),
            action_id=str(action.action_id),
            session_id=str(session_id),
            tool_name=action.tool_name,
            operation=action.operation,
            resource=action.resource,
            risk_level=action.risk_level.value,
            summary=action.summary,
            status=ApprovalRequestStatus.PENDING.value,
            created_at=utc_now(),
            resolved_at=None,
        )
        self._session.add(model)
        await self._session.flush()
        self._tracker.state_changes += 1
        return model

    async def get_request(self, request_id: UUID) -> ApprovalRequestModel | None:
        return await self._session.get(ApprovalRequestModel, str(request_id))

    async def resolve_request(
        self, request_id: UUID, status: ApprovalRequestStatus
    ) -> ApprovalRequestModel:
        if status is ApprovalRequestStatus.PENDING:
            raise ValueError("an approval request cannot be resolved to pending")
        model = await self.get_request(request_id)
        if model is None:
            raise RecordNotFoundError(f"approval request {request_id} was not found")
        model.status = status.value
        model.resolved_at = utc_now()
        self._tracker.state_changes += 1
        return model

    async def create_grant(
        self,
        grant: ApprovalGrant,
        *,
        approval_request_id: UUID | None = None,
    ) -> ApprovalGrantModel:
        model = ApprovalGrantModel(
            id=str(grant.grant_id),
            approval_request_id=str(approval_request_id) if approval_request_id else None,
            session_id=str(grant.session_id),
            tool_name=grant.tool_name,
            operation=grant.operation,
            resource_pattern=grant.resource_pattern,
            risk_level=grant.risk_level.value,
            issued_at=grant.issued_at,
            expires_at=grant.expires_at,
            revoked_at=None,
        )
        self._session.add(model)
        await self._session.flush()
        self._tracker.state_changes += 1
        return model

    async def revoke_grant(self, grant_id: UUID) -> ApprovalGrantModel:
        model = await self._session.get(ApprovalGrantModel, str(grant_id))
        if model is None:
            raise RecordNotFoundError(f"approval grant {grant_id} was not found")
        model.revoked_at = utc_now()
        self._tracker.state_changes += 1
        return model


class WorkflowRunRepository:
    def __init__(self, session: AsyncSession, tracker: _ChangeTracker) -> None:
        self._session = session
        self._tracker = tracker

    async def create(
        self,
        *,
        session_id: UUID,
        input_summary: str | None = None,
        run_id: UUID | None = None,
    ) -> WorkflowRunModel:
        now = utc_now()
        model = WorkflowRunModel(
            id=str(run_id or uuid4()),
            session_id=str(session_id),
            status=WorkflowRunStatus.PENDING.value,
            current_node=None,
            input_summary=input_summary,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        self._session.add(model)
        await self._session.flush()
        self._tracker.state_changes += 1
        return model

    async def get(self, run_id: UUID) -> WorkflowRunModel | None:
        return await self._session.get(WorkflowRunModel, str(run_id))

    async def update_status(
        self,
        run_id: UUID,
        status: WorkflowRunStatus,
        *,
        current_node: str | None = None,
    ) -> WorkflowRunModel:
        model = await self.get(run_id)
        if model is None:
            raise RecordNotFoundError(f"workflow run {run_id} was not found")
        model.status = status.value
        model.current_node = current_node
        model.updated_at = utc_now()
        if status in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }:
            model.completed_at = model.updated_at
        self._tracker.state_changes += 1
        return model


class AuditRepository:
    def __init__(self, session: AsyncSession, tracker: _ChangeTracker) -> None:
        self._session = session
        self._tracker = tracker

    async def append(
        self,
        *,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        session_id: UUID | None = None,
        run_id: UUID | None = None,
        approval_id: UUID | None = None,
        event_id: UUID | None = None,
    ) -> AuditEventModel:
        model = AuditEventModel(
            id=str(event_id or uuid4()),
            event_type=event_type,
            actor=actor,
            session_id=str(session_id) if session_id else None,
            run_id=str(run_id) if run_id else None,
            approval_id=str(approval_id) if approval_id else None,
            occurred_at=utc_now(),
            payload=payload or {},
        )
        self._session.add(model)
        self._tracker.audit_events += 1
        return model


class UnitOfWork:
    """One database transaction that requires auditing for state changes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory()
        self._tracker = _ChangeTracker()
        self.sessions = SessionRepository(self._session, self._tracker)
        self.approvals = ApprovalRepository(self._session, self._tracker)
        self.workflow_runs = WorkflowRunRepository(self._session, self._tracker)
        self.audit = AuditRepository(self._session, self._tracker)
        self._committed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        if not self._committed:
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        if self._tracker.state_changes and not self._tracker.audit_events:
            await self._session.rollback()
            raise MissingAuditEventError(
                "state-changing transactions must include at least one audit event"
            )
        try:
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise
        self._committed = True
