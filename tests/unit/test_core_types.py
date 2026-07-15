"""Tests for shared action contracts."""

import pytest
from pydantic import ValidationError

from personal_agent.core.types import ActionRequest, RiskLevel


def test_action_request_canonicalizes_documented_tool_shorthand() -> None:
    action = ActionRequest(
        tool_name="local_execution/write_file",
        operation="write_file",
        resource="/workspace/project",
        risk_level=RiskLevel.WRITE,
        summary="Create test.txt",
        arguments={"path": "test.txt", "content": "hello"},
    )

    assert action.tool_name == "local_execution"
    assert action.operation == "write_file"


def test_action_request_rejects_conflicting_combined_tool_name() -> None:
    with pytest.raises(ValidationError, match="conflicts with operation"):
        ActionRequest(
            tool_name="local_execution/write_file",
            operation="run_command",
            resource="/workspace/project",
            risk_level=RiskLevel.WRITE,
            summary="Ambiguous action",
        )
