"""Framework-independent contracts shared across transports and local tools."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for domain contracts."""

    return datetime.now(UTC)


class RiskLevel(StrEnum):
    """The approval threshold required for an external effect."""

    READ = "read"
    WRITE = "write"
    RISKY = "risky"


class ActionRequest(BaseModel):
    """A model-proposed action that must be checked by the policy layer."""

    action_id: UUID = Field(default_factory=uuid4)
    tool_name: str
    operation: str
    resource: str
    risk_level: RiskLevel
    summary: str


class ApprovalGrant(BaseModel):
    """A bounded user authorization issued for one session."""

    grant_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    tool_name: str
    operation: str
    resource_pattern: str
    risk_level: RiskLevel
    expires_at: datetime
    issued_at: datetime = Field(default_factory=utc_now)
