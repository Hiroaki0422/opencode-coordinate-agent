"""Transport-neutral lifecycle and workflow operations for the personal agent."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from langgraph.types import Command

from personal_agent.core.config import Settings
from personal_agent.execution import (
    DockerSandbox,
    LocalExecutionTool,
    OpenCodeTool,
    WorkspaceService,
)
from personal_agent.graph import AgentState, CompiledAgentGraph, open_agent_graph
from personal_agent.models import ConversationTurn, build_coordinator, health_check_coordinator
from personal_agent.observability import get_logger, redact_sensitive_text
from personal_agent.persistence import (
    MAX_CONVERSATION_MESSAGE_CHARS,
    ConversationMessageRole,
    Database,
    RecordNotFoundError,
)
from personal_agent.persistence.models import WorkflowRunStatus, utc_now
from personal_agent.policy import PolicyService
from personal_agent.tools import ResponseVerifier, ToolGateway
from personal_agent.tools.research import build_research_tool
from personal_agent.tools.todoist import TodoistTaskProvider, TodoistTool


@dataclass(frozen=True)
class AgentRunResult:
    """One submitted or resumed workflow result."""

    session_id: UUID
    run_id: UUID
    status: str | None
    response: str | None
    interrupts: tuple[Any, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "run_id": str(self.run_id),
            "status": "approval_required" if self.interrupts else self.status,
            "response": self.response,
            "interrupts": list(self.interrupts),
        }


@dataclass(frozen=True)
class PendingApproval:
    id: str
    summary: str
    risk_level: str


@dataclass(frozen=True)
class ConversationMessage:
    id: str
    run_id: UUID
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class SessionInspection:
    session_id: UUID
    status: str
    expires_at: datetime
    latest_run_id: UUID | None
    latest_run_status: str | None


@dataclass(frozen=True)
class RunInspection:
    run_id: str
    session_id: str
    status: str
    current_node: str | None
    pending_approvals: tuple[PendingApproval, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status,
            "current_node": self.current_node,
            "pending_approvals": [
                {
                    "id": item.id,
                    "summary": item.summary,
                    "risk_level": item.risk_level,
                }
                for item in self.pending_approvals
            ],
        }


class AgentRuntime:
    """Execute durable agent operations independently of a transport."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        graph: CompiledAgentGraph | None,
        actor: str,
    ) -> None:
        self._settings = settings
        self._database = database
        self._graph = graph
        self._actor = actor
        self._logger = get_logger(__name__)

    async def create_session(self) -> UUID:
        session_id = uuid4()
        expires_at = utc_now() + timedelta(
            minutes=self._settings.policy.session_ttl_minutes
        )
        async with self._database.unit_of_work() as unit_of_work:
            await unit_of_work.sessions.create(session_id=session_id, expires_at=expires_at)
            await unit_of_work.audit.append(
                event_type="session.created",
                actor=self._actor,
                session_id=session_id,
            )
            await unit_of_work.commit()
        return session_id

    async def submit(
        self,
        prompt: str,
        *,
        session_id: UUID | None = None,
    ) -> AgentRunResult:
        graph = self._require_graph()
        active_session_id = session_id or await self.create_session()
        history = await self._load_conversation_context(active_session_id)
        run_id = await self._create_workflow_run(
            session_id=active_session_id,
            prompt=prompt,
        )
        initial_state: AgentState = {
            "session_id": str(active_session_id),
            "run_id": str(run_id),
            "user_input": prompt,
            "conversation_history": [
                turn.model_dump(mode="json") for turn in history
            ],
        }
        config = cast(Any, {"configurable": {"thread_id": str(run_id)}})
        started_at = time.monotonic()
        try:
            raw_result = await graph.ainvoke(initial_state, config=config)
        except asyncio.CancelledError:
            await self._record_cancelled(active_session_id, run_id)
            raise
        duration_ms = round((time.monotonic() - started_at) * 1000)
        result = cast(dict[str, Any], raw_result)
        await self._record_graph_result(
            session_id=active_session_id,
            run_id=run_id,
            result=result,
            duration_ms=duration_ms,
        )
        return self._to_run_result(active_session_id, run_id, result)

    async def resume(self, run_id: UUID, *, approved: bool) -> AgentRunResult:
        graph = self._require_graph()
        async with self._database.unit_of_work() as unit_of_work:
            run = await unit_of_work.workflow_runs.get(run_id)
            if run is None:
                raise RecordNotFoundError(f"workflow run {run_id} was not found")
            session_id = UUID(run.session_id)
        config = cast(Any, {"configurable": {"thread_id": str(run_id)}})
        started_at = time.monotonic()
        try:
            raw_result = await graph.ainvoke(Command(resume=approved), config=config)
        except asyncio.CancelledError:
            await self._record_cancelled(session_id, run_id)
            raise
        duration_ms = round((time.monotonic() - started_at) * 1000)
        result = cast(dict[str, Any], raw_result)
        await self._record_graph_result(
            session_id=session_id,
            run_id=run_id,
            result=result,
            duration_ms=duration_ms,
        )
        return self._to_run_result(session_id, run_id, result)

    async def session_status(self, session_id: UUID) -> SessionInspection:
        async with self._database.unit_of_work() as unit_of_work:
            session = await unit_of_work.sessions.get(session_id)
            if session is None:
                raise RecordNotFoundError(f"session {session_id} was not found")
            runs = await unit_of_work.workflow_runs.list_for_session(session_id)
            latest = runs[0] if runs else None
            return SessionInspection(
                session_id=session_id,
                status=session.status,
                expires_at=session.expires_at,
                latest_run_id=UUID(latest.id) if latest is not None else None,
                latest_run_status=latest.status if latest is not None else None,
            )

    async def conversation_history(
        self,
        session_id: UUID,
    ) -> tuple[ConversationMessage, ...]:
        async with self._database.unit_of_work() as unit_of_work:
            if await unit_of_work.sessions.get(session_id) is None:
                raise RecordNotFoundError(f"session {session_id} was not found")
            messages = await unit_of_work.conversations.list_for_session(session_id)
            return tuple(
                ConversationMessage(
                    id=item.id,
                    run_id=UUID(item.run_id),
                    role=item.role,
                    content=item.content,
                    created_at=item.created_at,
                )
                for item in messages
            )

    async def clear_conversation_history(self, session_id: UUID) -> int:
        async with self._database.unit_of_work() as unit_of_work:
            if await unit_of_work.sessions.get(session_id) is None:
                raise RecordNotFoundError(f"session {session_id} was not found")
            deleted = await unit_of_work.conversations.delete_for_session(session_id)
            await unit_of_work.audit.append(
                event_type="conversation.history_cleared",
                actor=self._actor,
                session_id=session_id,
                payload={"deleted_messages": deleted},
            )
            await unit_of_work.commit()
        return deleted

    async def inspect(self, run_id: UUID) -> RunInspection:
        async with self._database.unit_of_work() as unit_of_work:
            run = await unit_of_work.workflow_runs.get(run_id)
            if run is None:
                raise RecordNotFoundError(f"workflow run {run_id} was not found")
            pending = await unit_of_work.approvals.list_pending_requests(
                session_id=UUID(run.session_id)
            )
            return RunInspection(
                run_id=run.id,
                session_id=run.session_id,
                status=run.status,
                current_node=run.current_node,
                pending_approvals=tuple(
                    PendingApproval(
                        id=item.id,
                        summary=item.summary,
                        risk_level=item.risk_level,
                    )
                    for item in pending
                ),
            )

    async def _create_workflow_run(self, *, session_id: UUID, prompt: str) -> UUID:
        run_id = uuid4()
        stored_prompt = redact_sensitive_text(self._settings, prompt)
        async with self._database.unit_of_work() as unit_of_work:
            if await unit_of_work.sessions.get(session_id) is None:
                raise RecordNotFoundError(f"session {session_id} was not found")
            await unit_of_work.workflow_runs.create(
                session_id=session_id,
                input_summary=stored_prompt[:500],
                run_id=run_id,
            )
            message = await unit_of_work.conversations.create(
                session_id=session_id,
                run_id=run_id,
                role=ConversationMessageRole.USER,
                content=stored_prompt,
            )
            await unit_of_work.audit.append(
                event_type="workflow.created",
                actor=self._actor,
                session_id=session_id,
                run_id=run_id,
                payload=self._message_audit_payload(message.id, stored_prompt),
            )
            await unit_of_work.commit()
        return run_id

    async def _record_graph_result(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        result: dict[str, Any],
        duration_ms: int,
    ) -> None:
        interrupts = self._interrupt_values(result)
        if interrupts:
            status = WorkflowRunStatus.PAUSED
            current_node = "approval"
        else:
            status = (
                WorkflowRunStatus.FAILED
                if result.get("status") == "failed"
                else WorkflowRunStatus.SUCCEEDED
            )
            current_node = None
        response = result.get("response")
        async with self._database.unit_of_work() as unit_of_work:
            await unit_of_work.workflow_runs.update_status(
                run_id,
                status,
                current_node=current_node,
            )
            await unit_of_work.audit.append(
                event_type="workflow.status_changed",
                actor=self._actor,
                session_id=session_id,
                run_id=run_id,
                payload={
                    "status": status.value,
                    "current_node": current_node,
                    "duration_ms": duration_ms,
                    "provider_route": self._provider_route(),
                },
            )
            if not interrupts and isinstance(response, str) and response.strip():
                stored_response = self._bounded_message(
                    redact_sensitive_text(self._settings, response)
                )
                message = await unit_of_work.conversations.create(
                    session_id=session_id,
                    run_id=run_id,
                    role=ConversationMessageRole.ASSISTANT,
                    content=stored_response,
                )
                await unit_of_work.audit.append(
                    event_type="conversation.message_created",
                    actor=self._actor,
                    session_id=session_id,
                    run_id=run_id,
                    payload=self._message_audit_payload(message.id, stored_response),
                )
            await unit_of_work.commit()
        self._logger.info(
            "conversation.turn_completed",
            session_id=str(session_id),
            run_id=str(run_id),
            status=status.value,
            duration_ms=duration_ms,
            provider_route=self._provider_route(),
        )

    async def _record_cancelled(self, session_id: UUID, run_id: UUID) -> None:
        async with self._database.unit_of_work() as unit_of_work:
            await unit_of_work.workflow_runs.update_status(
                run_id,
                WorkflowRunStatus.CANCELLED,
            )
            await unit_of_work.audit.append(
                event_type="workflow.cancelled",
                actor=self._actor,
                session_id=session_id,
                run_id=run_id,
                payload={"provider_route": self._provider_route()},
            )
            await unit_of_work.commit()
        self._logger.info(
            "conversation.turn_cancelled",
            session_id=str(session_id),
            run_id=str(run_id),
            provider_route=self._provider_route(),
        )

    async def _load_conversation_context(
        self,
        session_id: UUID,
    ) -> tuple[ConversationTurn, ...]:
        async with self._database.unit_of_work() as unit_of_work:
            if await unit_of_work.sessions.get(session_id) is None:
                raise RecordNotFoundError(f"session {session_id} was not found")
            messages = await unit_of_work.conversations.list_for_session(session_id)
            snapshots = [
                (item.run_id, item.role, item.content)
                for item in messages
            ]

        pending_users: dict[str, str] = {}
        complete_turns: list[ConversationTurn] = []
        for run_id, role, content in snapshots:
            if role == ConversationMessageRole.USER.value:
                pending_users[run_id] = content
            elif role == ConversationMessageRole.ASSISTANT.value and run_id in pending_users:
                complete_turns.append(
                    ConversationTurn(
                        user=pending_users.pop(run_id),
                        assistant=content,
                    )
                )

        selected: list[ConversationTurn] = []
        selected_chars = 0
        for turn in reversed(complete_turns):
            turn_chars = len(turn.user) + len(turn.assistant)
            if len(selected) >= self._settings.conversation.max_turns:
                break
            if selected_chars + turn_chars > self._settings.conversation.max_context_chars:
                break
            selected.append(turn)
            selected_chars += turn_chars
        selected.reverse()
        return tuple(selected)

    def _require_graph(self) -> CompiledAgentGraph:
        if self._graph is None:
            raise RuntimeError("agent operations were not initialized for this runtime")
        return self._graph

    @staticmethod
    def _interrupt_values(result: dict[str, Any]) -> tuple[Any, ...]:
        interrupts = result.get("__interrupt__", ())
        return tuple(getattr(item, "value", item) for item in interrupts)

    @classmethod
    def _to_run_result(
        cls,
        session_id: UUID,
        run_id: UUID,
        result: dict[str, Any],
    ) -> AgentRunResult:
        interrupts = cls._interrupt_values(result)
        response = result.get("response")
        return AgentRunResult(
            session_id=session_id,
            run_id=run_id,
            status=result.get("status"),
            response=response if isinstance(response, str) else None,
            interrupts=interrupts,
        )

    @staticmethod
    def _message_audit_payload(message_id: str, content: str) -> dict[str, Any]:
        return {
            "message_id": message_id,
            "content_chars": len(content),
            "content_digest": hashlib.sha256(content.encode()).hexdigest(),
        }

    def _provider_route(self) -> list[str]:
        return [target.provider for target in self._settings.coordinator.models]

    @staticmethod
    def _bounded_message(content: str) -> str:
        marker = "\n[conversation message truncated]"
        if len(content) <= MAX_CONVERSATION_MESSAGE_CHARS:
            return content
        return content[: MAX_CONVERSATION_MESSAGE_CHARS - len(marker)] + marker

def _build_tool_gateway(settings: Settings, database: Database) -> ToolGateway:
    gateway = ToolGateway(database)
    if settings.research.enabled:
        gateway.register(build_research_tool(settings.research))
    if settings.todoist.enabled:
        if settings.todoist.api_token is None:
            raise ValueError("Todoist API token is not configured")
        gateway.register(
            TodoistTool(
                TodoistTaskProvider(
                    api_token=settings.todoist.api_token.get_secret_value(),
                    base_url=settings.todoist.base_url,
                    timeout_seconds=settings.todoist.timeout_seconds,
                )
            )
        )
    if settings.local_execution.enabled:
        sandbox = DockerSandbox(settings.local_execution)
        workspaces = WorkspaceService(
            settings.local_execution.workspace_root,
            sandbox,
            settings.local_execution.repository_paths,
        )
        gateway.register(LocalExecutionTool(sandbox, workspaces))
        if settings.opencode.enabled:
            if settings.deepseek.api_key is None:
                raise ValueError("DeepSeek API key is not configured")
            gateway.register(
                OpenCodeTool(
                    settings=settings.opencode,
                    api_key=settings.deepseek.api_key.get_secret_value(),
                    sandbox=sandbox,
                    workspaces=workspaces,
                )
            )
    return gateway


@asynccontextmanager
async def open_agent_runtime(
    settings: Settings,
    *,
    actor: str,
    initialize_agent: bool = True,
) -> AsyncIterator[AgentRuntime]:
    """Open transport-neutral dependencies and release them as one lifecycle."""

    database = Database(settings.database_url)
    await database.initialize()
    gateway: ToolGateway | None = None
    try:
        if not initialize_agent:
            yield AgentRuntime(
                settings=settings,
                database=database,
                graph=None,
                actor=actor,
            )
            return

        coordinator = build_coordinator(settings)
        await health_check_coordinator(coordinator)
        gateway = _build_tool_gateway(settings, database)
        policy = PolicyService(database, settings.policy)
        verifier = ResponseVerifier()
        async with open_agent_graph(
            checkpoint_path=settings.checkpoint_path,
            coordinator=coordinator,
            policy=policy,
            gateway=gateway,
            verifier=verifier,
        ) as graph:
            yield AgentRuntime(
                settings=settings,
                database=database,
                graph=graph,
                actor=actor,
            )
    finally:
        if gateway is not None:
            await gateway.aclose()
        await database.dispose()
