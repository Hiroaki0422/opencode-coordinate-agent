"""Typer command-line interface for sessions, runs, and approvals."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import typer
from langgraph.types import Command

from personal_agent.core.config import Settings, get_settings
from personal_agent.graph import AgentState, open_agent_graph
from personal_agent.models import build_coordinator
from personal_agent.observability import configure_logging
from personal_agent.persistence import Database
from personal_agent.persistence.models import WorkflowRunStatus
from personal_agent.policy import PolicyService
from personal_agent.tools import ResponseVerifier, ToolGateway
from personal_agent.tools.research import build_research_tool
from personal_agent.tools.todoist import TodoistTaskProvider, TodoistTool

app = typer.Typer(help="Permission-gated personal AI agent.", no_args_is_help=True)
session_app = typer.Typer(help="Create and inspect bounded sessions.")
app.add_typer(session_app, name="session")


def _run_async[ResultT](coroutine: Coroutine[Any, Any, ResultT]) -> ResultT:
    return asyncio.run(coroutine)


def _settings() -> Settings:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(settings)
    return settings


async def _open_database(settings: Settings) -> Database:
    database = Database(settings.database_url)
    await database.initialize()
    return database


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
    return gateway


async def _create_session(database: Database, settings: Settings) -> UUID:
    session_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.policy.session_ttl_minutes)
    async with database.unit_of_work() as unit_of_work:
        await unit_of_work.sessions.create(session_id=session_id, expires_at=expires_at)
        await unit_of_work.audit.append(
            event_type="session.created",
            actor="cli",
            session_id=session_id,
        )
        await unit_of_work.commit()
    return session_id


async def _create_workflow_run(
    database: Database,
    *,
    session_id: UUID,
    prompt: str,
) -> UUID:
    run_id = uuid4()
    async with database.unit_of_work() as unit_of_work:
        if await unit_of_work.sessions.get(session_id) is None:
            raise typer.BadParameter(f"session {session_id} was not found")
        await unit_of_work.workflow_runs.create(
            session_id=session_id,
            input_summary=prompt[:500],
            run_id=run_id,
        )
        await unit_of_work.audit.append(
            event_type="workflow.created",
            actor="cli",
            session_id=session_id,
            run_id=run_id,
        )
        await unit_of_work.commit()
    return run_id


async def _record_run_status(
    database: Database,
    *,
    run_id: UUID,
    status: WorkflowRunStatus,
    current_node: str | None = None,
) -> None:
    async with database.unit_of_work() as unit_of_work:
        run = await unit_of_work.workflow_runs.update_status(
            run_id,
            status,
            current_node=current_node,
        )
        await unit_of_work.audit.append(
            event_type="workflow.status_changed",
            actor="cli",
            session_id=UUID(run.session_id),
            run_id=run_id,
            payload={"status": status.value, "current_node": current_node},
        )
        await unit_of_work.commit()


def _render_graph_result(result: dict[str, Any], *, session_id: UUID, run_id: UUID) -> str:
    interrupts = result.get("__interrupt__", ())
    interrupt_values = [getattr(item, "value", item) for item in interrupts]
    return json.dumps(
        {
            "session_id": str(session_id),
            "run_id": str(run_id),
            "status": "approval_required" if interrupt_values else result.get("status"),
            "response": result.get("response"),
            "interrupts": interrupt_values,
        },
        indent=2,
        default=str,
    )


@session_app.command("start")
def start_session() -> None:
    """Create a new bounded session."""

    async def execute() -> str:
        settings = _settings()
        database = await _open_database(settings)
        try:
            session_id = await _create_session(database, settings)
            return json.dumps({"session_id": str(session_id)}, indent=2)
        finally:
            await database.dispose()

    typer.echo(_run_async(execute()))


@app.command("run")
def run_request(
    prompt: str = typer.Argument(..., help="Request for the coordinator."),
    session_id: UUID | None = typer.Option(None, help="Existing session; created when omitted."),
) -> None:
    """Submit a request and pause if human approval is required."""

    async def execute() -> str:
        settings = _settings()
        database = await _open_database(settings)
        try:
            active_session_id = session_id or await _create_session(database, settings)
            run_id = await _create_workflow_run(
                database,
                session_id=active_session_id,
                prompt=prompt,
            )
            policy = PolicyService(database, settings.policy)
            coordinator = build_coordinator(settings)
            gateway = _build_tool_gateway(settings, database)
            verifier = ResponseVerifier()
            initial_state: AgentState = {
                "session_id": str(active_session_id),
                "run_id": str(run_id),
                "user_input": prompt,
            }
            config = cast(Any, {"configurable": {"thread_id": str(run_id)}})
            try:
                async with open_agent_graph(
                    checkpoint_path=settings.checkpoint_path,
                    coordinator=coordinator,
                    policy=policy,
                    gateway=gateway,
                    verifier=verifier,
                ) as graph:
                    raw_result = await graph.ainvoke(initial_state, config=config)
            finally:
                await gateway.aclose()
            result = cast(dict[str, Any], raw_result)
            if result.get("__interrupt__"):
                await _record_run_status(
                    database,
                    run_id=run_id,
                    status=WorkflowRunStatus.PAUSED,
                    current_node="approval",
                )
            else:
                terminal_status = (
                    WorkflowRunStatus.FAILED
                    if result.get("status") == "failed"
                    else WorkflowRunStatus.SUCCEEDED
                )
                await _record_run_status(
                    database,
                    run_id=run_id,
                    status=terminal_status,
                )
            return _render_graph_result(
                result,
                session_id=active_session_id,
                run_id=run_id,
            )
        finally:
            await database.dispose()

    typer.echo(_run_async(execute()))


async def _resume_run(run_id: UUID, *, approved: bool) -> str:
    settings = _settings()
    database = await _open_database(settings)
    try:
        async with database.unit_of_work() as unit_of_work:
            run = await unit_of_work.workflow_runs.get(run_id)
            if run is None:
                raise typer.BadParameter(f"workflow run {run_id} was not found")
            session_id = UUID(run.session_id)
        policy = PolicyService(database, settings.policy)
        coordinator = build_coordinator(settings)
        gateway = _build_tool_gateway(settings, database)
        verifier = ResponseVerifier()
        config = cast(Any, {"configurable": {"thread_id": str(run_id)}})
        try:
            async with open_agent_graph(
                checkpoint_path=settings.checkpoint_path,
                coordinator=coordinator,
                policy=policy,
                gateway=gateway,
                verifier=verifier,
            ) as graph:
                raw_result = await graph.ainvoke(Command(resume=approved), config=config)
        finally:
            await gateway.aclose()
        result = cast(dict[str, Any], raw_result)
        terminal_status = (
            WorkflowRunStatus.FAILED
            if result.get("status") == "failed"
            else WorkflowRunStatus.SUCCEEDED
        )
        await _record_run_status(
            database,
            run_id=run_id,
            status=terminal_status,
        )
        return _render_graph_result(result, session_id=session_id, run_id=run_id)
    finally:
        await database.dispose()


@app.command("approve")
def approve_run(run_id: UUID) -> None:
    """Approve and resume a paused workflow run."""

    typer.echo(_run_async(_resume_run(run_id, approved=True)))


@app.command("deny")
def deny_run(run_id: UUID) -> None:
    """Deny and resume a paused workflow run."""

    typer.echo(_run_async(_resume_run(run_id, approved=False)))


@app.command("inspect")
def inspect_run(run_id: UUID) -> None:
    """Inspect durable workflow and pending approval state."""

    async def execute() -> str:
        settings = _settings()
        database = await _open_database(settings)
        try:
            async with database.unit_of_work() as unit_of_work:
                run = await unit_of_work.workflow_runs.get(run_id)
                if run is None:
                    raise typer.BadParameter(f"workflow run {run_id} was not found")
                pending = await unit_of_work.approvals.list_pending_requests(
                    session_id=UUID(run.session_id)
                )
                payload = {
                    "run_id": run.id,
                    "session_id": run.session_id,
                    "status": run.status,
                    "current_node": run.current_node,
                    "pending_approvals": [
                        {
                            "id": item.id,
                            "summary": item.summary,
                            "risk_level": item.risk_level,
                        }
                        for item in pending
                    ],
                }
            return json.dumps(payload, indent=2)
        finally:
            await database.dispose()

    typer.echo(_run_async(execute()))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
