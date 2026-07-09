"""Serializable state shared by the P0 LangGraph workflow."""

from typing import Any, NotRequired, TypedDict


class AgentState(TypedDict):
    session_id: str
    run_id: str
    user_input: str
    decision_message: NotRequired[str]
    action: NotRequired[dict[str, Any] | None]
    policy_decision: NotRequired[str]
    policy_reason: NotRequired[str]
    approval_request_id: NotRequired[str | None]
    status: NotRequired[str]
    response: NotRequired[str]
