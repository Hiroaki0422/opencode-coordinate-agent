"""Provider-neutral tool requests, results, and evidence."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from personal_agent.core.types import ActionRequest


class ToolEvidence(BaseModel):
    """One verifiable artifact returned by a tool."""

    kind: str
    identifier: str
    title: str | None = None
    url: str | None = None
    excerpt: str | None = None


class ToolExecutionResult(BaseModel):
    """Structured result used by verification and audit layers."""

    tool_name: str
    operation: str
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[ToolEvidence] = Field(default_factory=list)
    external_ids: list[str] = Field(default_factory=list)
    audit_data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ToolAdapter(Protocol):
    name: str

    async def execute(self, action: ActionRequest) -> ToolExecutionResult:
        """Execute one policy-authorized action."""

    async def aclose(self) -> None:
        """Release adapter resources."""
