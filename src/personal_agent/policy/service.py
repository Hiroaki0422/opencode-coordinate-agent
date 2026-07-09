"""Deterministic session, risk, and approval enforcement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from fnmatch import fnmatchcase
from uuid import UUID

from pydantic import BaseModel

from personal_agent.core.config import PolicySettings
from personal_agent.core.types import ActionRequest, ApprovalGrant, RiskLevel
from personal_agent.persistence import Database
from personal_agent.persistence.models import (
    ApprovalGrantModel,
    ApprovalRequestModel,
    ApprovalRequestStatus,
    SessionModel,
    SessionStatus,
)


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyResult(BaseModel):
    decision: PolicyDecision
    reason: str
    approval_request_id: UUID | None = None


class ApprovalExpiredError(RuntimeError):
    """Raised when an approval response arrives after its allowed window."""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class PolicyService:
    """Evaluate actions and persist human approval decisions."""

    def __init__(self, database: Database, settings: PolicySettings) -> None:
        self._database = database
        self._settings = settings

    async def authorize(self, *, session_id: UUID, action: ActionRequest) -> PolicyResult:
        now = datetime.now(UTC)
        async with self._database.unit_of_work() as unit_of_work:
            session = await unit_of_work.sessions.get(session_id)
            denial = self._validate_session(session, now)
            if denial is not None:
                return denial

            if action.risk_level is RiskLevel.READ:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason="read actions are allowed",
                )

            if action.risk_level is RiskLevel.WRITE:
                grants = await unit_of_work.approvals.list_active_grants(
                    session_id=session_id,
                    at=now,
                )
                if any(self._grant_matches(grant, action) for grant in grants):
                    return PolicyResult(
                        decision=PolicyDecision.ALLOW,
                        reason="an active session grant matches this write action",
                    )

            existing = await unit_of_work.approvals.get_request_by_action_id(action.action_id)
            if existing is not None:
                return PolicyResult(
                    decision=PolicyDecision.REQUIRE_APPROVAL,
                    reason=f"approval request is {existing.status}",
                    approval_request_id=UUID(existing.id),
                )

            request = await unit_of_work.approvals.create_request(
                session_id=session_id,
                action=action,
            )
            request_id = UUID(request.id)
            await unit_of_work.audit.append(
                event_type="approval.requested",
                actor="policy",
                session_id=session_id,
                approval_id=request_id,
                payload={
                    "action_id": str(action.action_id),
                    "tool_name": action.tool_name,
                    "operation": action.operation,
                    "resource": action.resource,
                    "risk_level": action.risk_level.value,
                },
            )
            await unit_of_work.commit()
            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason="this action requires human approval",
                approval_request_id=request_id,
            )

    async def approve(self, request_id: UUID) -> ApprovalRequestModel:
        now = datetime.now(UTC)
        async with self._database.unit_of_work() as unit_of_work:
            request = await unit_of_work.approvals.get_request(request_id)
            if request is None:
                raise LookupError(f"approval request {request_id} was not found")
            if request.status == ApprovalRequestStatus.APPROVED.value:
                return request
            if request.status != ApprovalRequestStatus.PENDING.value:
                raise ValueError(f"approval request is already {request.status}")
            if self._request_expired(request, now):
                await unit_of_work.approvals.resolve_request(
                    request_id,
                    ApprovalRequestStatus.DENIED,
                )
                await unit_of_work.audit.append(
                    event_type="approval.expired",
                    actor="human",
                    session_id=UUID(request.session_id),
                    approval_id=request_id,
                )
                await unit_of_work.commit()
                raise ApprovalExpiredError(f"approval request {request_id} has expired")

            resolved = await unit_of_work.approvals.resolve_request(
                request_id,
                ApprovalRequestStatus.APPROVED,
            )
            if request.risk_level == RiskLevel.WRITE.value:
                session = await unit_of_work.sessions.get(UUID(request.session_id))
                if session is None:
                    raise LookupError(f"session {request.session_id} was not found")
                grant_expiry = min(
                    _as_utc(session.expires_at),
                    now + timedelta(minutes=self._settings.session_ttl_minutes),
                )
                await unit_of_work.approvals.create_grant(
                    ApprovalGrant(
                        session_id=UUID(request.session_id),
                        tool_name=request.tool_name,
                        operation=request.operation,
                        resource_pattern=request.resource,
                        risk_level=RiskLevel.WRITE,
                        issued_at=now,
                        expires_at=grant_expiry,
                    ),
                    approval_request_id=request_id,
                )
            await unit_of_work.audit.append(
                event_type="approval.approved",
                actor="human",
                session_id=UUID(request.session_id),
                approval_id=request_id,
            )
            await unit_of_work.commit()
            return resolved

    async def deny(self, request_id: UUID) -> ApprovalRequestModel:
        async with self._database.unit_of_work() as unit_of_work:
            request = await unit_of_work.approvals.get_request(request_id)
            if request is None:
                raise LookupError(f"approval request {request_id} was not found")
            if request.status == ApprovalRequestStatus.DENIED.value:
                return request
            if request.status != ApprovalRequestStatus.PENDING.value:
                raise ValueError(f"approval request is already {request.status}")
            resolved = await unit_of_work.approvals.resolve_request(
                request_id,
                ApprovalRequestStatus.DENIED,
            )
            await unit_of_work.audit.append(
                event_type="approval.denied",
                actor="human",
                session_id=UUID(request.session_id),
                approval_id=request_id,
            )
            await unit_of_work.commit()
            return resolved

    async def revoke(self, grant_id: UUID) -> None:
        async with self._database.unit_of_work() as unit_of_work:
            grant = await unit_of_work.approvals.get_grant(grant_id)
            if grant is None:
                raise LookupError(f"approval grant {grant_id} was not found")
            if grant.revoked_at is not None:
                return
            await unit_of_work.approvals.revoke_grant(grant_id)
            await unit_of_work.audit.append(
                event_type="approval.revoked",
                actor="human",
                session_id=UUID(grant.session_id),
                approval_id=grant_id,
            )
            await unit_of_work.commit()

    def _validate_session(
        self, session: SessionModel | None, now: datetime
    ) -> PolicyResult | None:
        if session is None:
            return PolicyResult(decision=PolicyDecision.DENY, reason="session was not found")
        if session.status != SessionStatus.ACTIVE.value:
            return PolicyResult(decision=PolicyDecision.DENY, reason="session is not active")
        if _as_utc(session.expires_at) <= now:
            return PolicyResult(decision=PolicyDecision.DENY, reason="session has expired")
        return None

    def _request_expired(self, request: ApprovalRequestModel, now: datetime) -> bool:
        expires_at = _as_utc(request.created_at) + timedelta(
            minutes=self._settings.approval_ttl_minutes
        )
        return expires_at <= now

    @staticmethod
    def _grant_matches(grant: ApprovalGrantModel, action: ActionRequest) -> bool:
        return (
            grant.tool_name == action.tool_name
            and grant.operation == action.operation
            and grant.risk_level == action.risk_level.value
            and fnmatchcase(action.resource, grant.resource_pattern)
        )
