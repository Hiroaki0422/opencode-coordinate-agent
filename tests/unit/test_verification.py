"""Tests for evidence-aware response verification."""

from typing import Any

from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.models import CoordinatorDecision, GroundedResponse
from personal_agent.tools import ResponseVerifier, ToolEvidence, ToolExecutionResult


class FakeCoordinator:
    def __init__(self, citations: list[str]) -> None:
        self._citations = citations

    async def decide(
        self,
        user_input: str,
        *,
        history: Any = (),
    ) -> CoordinatorDecision:
        del history
        return CoordinatorDecision(message=user_input)

    async def compose(
        self,
        user_input: str,
        evidence: list[dict[str, Any]],
        *,
        history: Any = (),
    ) -> GroundedResponse:
        del user_input, evidence, history
        return GroundedResponse(answer="Grounded fact [1].", citations=self._citations)


async def test_failed_tool_result_never_claims_success() -> None:
    result = await ResponseVerifier().verify(
        user_input="Create task",
        decision_message="I will create it.",
        action=ActionRequest(
            tool_name="todoist",
            operation="create_task",
            resource="inbox",
            risk_level=RiskLevel.WRITE,
            summary="Create task",
        ),
        result=ToolExecutionResult(
            tool_name="todoist",
            operation="create_task",
            success=False,
            error="provider unavailable",
        ),
        coordinator=FakeCoordinator([]),
    )

    assert result.success is False
    assert "failed" in result.response
    assert "provider unavailable" in result.response


async def test_todoist_mutation_requires_external_identifier() -> None:
    verification = await ResponseVerifier().verify(
        user_input="Create task",
        decision_message="Created.",
        action=ActionRequest(
            tool_name="todoist",
            operation="create_task",
            resource="inbox",
            risk_level=RiskLevel.WRITE,
            summary="Create task",
        ),
        result=ToolExecutionResult(
            tool_name="todoist",
            operation="create_task",
            success=True,
        ),
        coordinator=FakeCoordinator([]),
    )

    assert verification.success is False
    assert "identifier" in verification.response


async def test_research_requires_valid_citations_and_renders_sources() -> None:
    action = ActionRequest(
        tool_name="web_research",
        operation="search",
        resource="query",
        risk_level=RiskLevel.READ,
        summary="Research",
    )
    tool_result = ToolExecutionResult(
        tool_name="web_research",
        operation="search",
        success=True,
        evidence=[
            ToolEvidence(
                kind="web_source",
                identifier="1",
                title="Source",
                url="https://example.com",
            )
        ],
    )

    invalid = await ResponseVerifier().verify(
        user_input="Question",
        decision_message="Researching.",
        action=action,
        result=tool_result,
        coordinator=FakeCoordinator(["99"]),
    )
    valid = await ResponseVerifier().verify(
        user_input="Question",
        decision_message="Researching.",
        action=action,
        result=tool_result,
        coordinator=FakeCoordinator(["1"]),
    )

    assert invalid.success is False
    assert valid.success is True
    assert "Synthesis:" in valid.response
    assert "Retrieved sources:" in valid.response
    assert "https://example.com" in valid.response


async def test_opencode_requires_verified_changes_and_passing_tests() -> None:
    action = ActionRequest(
        tool_name="opencode",
        operation="code_task",
        resource="/workspace/project",
        risk_level=RiskLevel.RISKY,
        summary="Update app",
    )
    result = ToolExecutionResult(
        tool_name="opencode",
        operation="code_task",
        success=True,
        data={
            "changed_files": ["app.py"],
            "tests": [{"command": ["pytest"], "exit_code": 0}],
            "report": "Implemented the change.",
            "requested_change_verified": True,
        },
        external_ids=["app.py"],
    )

    verification = await ResponseVerifier().verify(
        user_input="Update app",
        decision_message="Delegating the change.",
        action=action,
        result=result,
        coordinator=FakeCoordinator([]),
    )

    assert verification.success is True
    assert "app.py" in verification.response
    assert "tests passed" in verification.response
