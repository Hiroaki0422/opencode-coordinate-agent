"""Serializable state shared by the P0 LangGraph workflow."""

from typing import Any, NotRequired, TypedDict


class AgentState(TypedDict):
    session_id: str
    run_id: str
    user_input: str
    conversation_history: NotRequired[list[dict[str, str]]]
    decision_message: NotRequired[str]
    action: NotRequired[dict[str, Any] | None]
    policy_decision: NotRequired[str]
    policy_reason: NotRequired[str]
    approval_request_id: NotRequired[str | None]
    approval_expires_at: NotRequired[str | None]
    tool_result: NotRequired[dict[str, Any]]
    status: NotRequired[str]
    response: NotRequired[str]
