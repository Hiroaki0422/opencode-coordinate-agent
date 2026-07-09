"""Checkpointed coordinator and approval workflow."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from personal_agent.core.types import ActionRequest
from personal_agent.graph.state import AgentState
from personal_agent.models import Coordinator
from personal_agent.policy import PolicyDecision, PolicyService

CompiledAgentGraph = CompiledStateGraph[AgentState, None, AgentState, AgentState]


def build_agent_graph(
    *,
    coordinator: Coordinator,
    policy: PolicyService,
    checkpointer: BaseCheckpointSaver[Any],
) -> CompiledAgentGraph:
    """Compile the P0 graph around injected model and policy services."""

    async def coordinate(state: AgentState) -> dict[str, Any]:
        decision = await coordinator.decide(state["user_input"])
        return {
            "decision_message": decision.message,
            "action": decision.action.model_dump(mode="json") if decision.action else None,
            "status": "planned",
        }

    def route_after_coordinate(state: AgentState) -> Literal["policy", "respond"]:
        return "policy" if state.get("action") is not None else "respond"

    async def evaluate_policy(state: AgentState) -> dict[str, Any]:
        raw_action = state.get("action")
        if raw_action is None:
            raise ValueError("policy node requires an action")
        action = ActionRequest.model_validate(raw_action)
        result = await policy.authorize(
            session_id=UUID(state["session_id"]),
            action=action,
        )
        return {
            "policy_decision": result.decision.value,
            "policy_reason": result.reason,
            "approval_request_id": (
                str(result.approval_request_id) if result.approval_request_id else None
            ),
        }

    def route_after_policy(
        state: AgentState,
    ) -> Literal["approval", "authorized", "denied"]:
        decision = PolicyDecision(state["policy_decision"])
        if decision is PolicyDecision.REQUIRE_APPROVAL:
            return "approval"
        if decision is PolicyDecision.ALLOW:
            return "authorized"
        return "denied"

    async def request_approval(
        state: AgentState,
    ) -> Command[Literal["authorized", "denied"]]:
        request_id_value = state.get("approval_request_id")
        if request_id_value is None:
            raise ValueError("approval node requires an approval request id")
        action = state.get("action") or {}
        approved = bool(
            interrupt(
                {
                    "approval_request_id": request_id_value,
                    "summary": action.get("summary"),
                    "tool_name": action.get("tool_name"),
                    "operation": action.get("operation"),
                    "resource": action.get("resource"),
                    "risk_level": action.get("risk_level"),
                }
            )
        )
        request_id = UUID(request_id_value)
        if approved:
            await policy.approve(request_id)
            return Command(
                goto="authorized",
                update={"policy_decision": PolicyDecision.ALLOW.value},
            )
        await policy.deny(request_id)
        return Command(
            goto="denied",
            update={
                "policy_decision": PolicyDecision.DENY.value,
                "policy_reason": "human denied the requested action",
            },
        )

    def respond(state: AgentState) -> dict[str, Any]:
        return {
            "status": "completed",
            "response": state["decision_message"],
        }

    def authorized(state: AgentState) -> dict[str, Any]:
        return {
            "status": "authorized",
            "response": (
                f"{state['decision_message']}\n\n"
                "The proposed action is authorized and ready for a tool adapter."
            ),
        }

    def denied(state: AgentState) -> dict[str, Any]:
        reason = state.get("policy_reason", "the action was denied")
        return {
            "status": "denied",
            "response": f"{state['decision_message']}\n\nAction denied: {reason}.",
        }

    builder = StateGraph(AgentState)
    builder.add_node("coordinate", coordinate)
    builder.add_node("policy", evaluate_policy)
    builder.add_node("approval", request_approval)
    builder.add_node("respond", respond)
    builder.add_node("authorized", authorized)
    builder.add_node("denied", denied)
    builder.add_edge(START, "coordinate")
    builder.add_conditional_edges("coordinate", route_after_coordinate)
    builder.add_conditional_edges("policy", route_after_policy)
    builder.add_edge("respond", END)
    builder.add_edge("authorized", END)
    builder.add_edge("denied", END)
    return builder.compile(checkpointer=checkpointer)
