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
from personal_agent.models import ConversationTurn, Coordinator
from personal_agent.policy import PolicyDecision, PolicyService
from personal_agent.tools import ResponseVerifier, ToolExecutionResult, ToolGateway

CompiledAgentGraph = CompiledStateGraph[AgentState, None, AgentState, AgentState]


def build_agent_graph(
    *,
    coordinator: Coordinator,
    policy: PolicyService,
    checkpointer: BaseCheckpointSaver[Any],
    gateway: ToolGateway | None = None,
    verifier: ResponseVerifier | None = None,
) -> CompiledAgentGraph:
    """Compile the P0 graph around injected model and policy services."""

    async def coordinate(state: AgentState) -> dict[str, Any]:
        history = tuple(
            ConversationTurn.model_validate(item)
            for item in state.get("conversation_history", [])
        )
        active_workspace = state.get("active_workspace")
        if active_workspace is None:
            decision = await coordinator.decide(state["user_input"], history=history)
        else:
            decision = await coordinator.decide(
                state["user_input"],
                history=history,
                active_workspace=active_workspace,
            )
        action = decision.action
        if action is not None and active_workspace is not None:
            normalized_resource = action.resource.strip().casefold()
            if normalized_resource in {
                "current workspace",
                "the current workspace",
                "this workspace",
                "workspace",
                ".",
            }:
                action = action.model_copy(update={"resource": active_workspace})
        return {
            "decision_message": decision.message,
            "action": action.model_dump(mode="json") if action else None,
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
            "approval_expires_at": (
                result.approval_expires_at.isoformat()
                if result.approval_expires_at is not None
                else None
            ),
        }

    def route_after_policy(
        state: AgentState,
    ) -> Literal["approval", "execute", "authorized", "denied"]:
        decision = PolicyDecision(state["policy_decision"])
        if decision is PolicyDecision.REQUIRE_APPROVAL:
            return "approval"
        if decision is PolicyDecision.ALLOW:
            return "execute" if gateway is not None else "authorized"
        return "denied"

    async def request_approval(
        state: AgentState,
    ) -> Command[Literal["execute", "authorized", "denied"]]:
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
                    "reason": state.get("policy_reason"),
                    "expires_at": state.get("approval_expires_at"),
                }
            )
        )
        request_id = UUID(request_id_value)
        if approved:
            await policy.approve(request_id)
            return Command(
                goto="execute" if gateway is not None else "authorized",
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

    async def execute_tool(state: AgentState) -> dict[str, Any]:
        if gateway is None:
            raise ValueError("tool execution requires a gateway")
        raw_action = state.get("action")
        if raw_action is None:
            raise ValueError("tool execution requires an action")
        action = ActionRequest.model_validate(raw_action)
        result = await gateway.execute(
            session_id=UUID(state["session_id"]),
            run_id=UUID(state["run_id"]),
            action=action,
        )
        return {"tool_result": result.model_dump(mode="json"), "status": "tool_executed"}

    async def verify_tool_result(state: AgentState) -> dict[str, Any]:
        if verifier is None:
            raise ValueError("tool execution requires a response verifier")
        raw_action = state.get("action")
        raw_result = state.get("tool_result")
        if raw_action is None or raw_result is None:
            raise ValueError("verification requires an action and tool result")
        verification = await verifier.verify(
            user_input=state["user_input"],
            decision_message=state["decision_message"],
            action=ActionRequest.model_validate(raw_action),
            result=ToolExecutionResult.model_validate(raw_result),
            coordinator=coordinator,
            history=tuple(
                ConversationTurn.model_validate(item)
                for item in state.get("conversation_history", [])
            ),
        )
        return {
            "status": "completed" if verification.success else "failed",
            "response": verification.response,
        }

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
    builder.add_node("execute", execute_tool)
    builder.add_node("verify", verify_tool_result)
    builder.add_node("respond", respond)
    builder.add_node("authorized", authorized)
    builder.add_node("denied", denied)
    builder.add_edge(START, "coordinate")
    builder.add_conditional_edges("coordinate", route_after_coordinate)
    builder.add_conditional_edges("policy", route_after_policy)
    builder.add_edge("respond", END)
    builder.add_edge("execute", "verify")
    builder.add_edge("verify", END)
    builder.add_edge("authorized", END)
    builder.add_edge("denied", END)
    return builder.compile(checkpointer=checkpointer)
