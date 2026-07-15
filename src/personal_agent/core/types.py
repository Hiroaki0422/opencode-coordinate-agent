"""Framework-independent contracts shared across transports and local tools."""

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


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
    arguments: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def canonicalize_tool_name(cls, value: Any) -> Any:
        """Convert documented tool/operation shorthand into separate fields."""

        if not isinstance(value, Mapping):
            return value
        tool_name = value.get("tool_name")
        if not isinstance(tool_name, str) or "/" not in tool_name:
            return value
        parts = tool_name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("tool_name must contain only the adapter name")
        adapter_name, shorthand_operation = parts
        operation = value.get("operation")
        if operation is not None and operation != shorthand_operation:
            raise ValueError("combined tool_name conflicts with operation")
        canonical = dict(value)
        canonical["tool_name"] = adapter_name
        canonical["operation"] = shorthand_operation
        return canonical


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
