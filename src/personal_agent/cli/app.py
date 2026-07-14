"""Typer command-line interface for sessions, runs, and approvals."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any
from uuid import UUID

import typer

from personal_agent.application import open_agent_runtime
from personal_agent.cli.chat import ConsoleTerminal, InteractiveChat
from personal_agent.core.config import Settings, get_settings
from personal_agent.models import CodexCliRunner
from personal_agent.observability import configure_logging
from personal_agent.persistence import RecordNotFoundError

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


@session_app.command("start")
def start_session() -> None:
    """Create a new bounded session."""

    async def execute() -> str:
        settings = _settings()
        async with open_agent_runtime(
            settings,
            actor="cli",
            initialize_agent=False,
        ) as runtime:
            session_id = await runtime.create_session()
            return json.dumps({"session_id": str(session_id)}, indent=2)

    typer.echo(_run_async(execute()))


@app.command("codex-health")
def codex_health() -> None:
    """Verify Codex CLI version and ChatGPT subscription login without inference."""

    async def execute() -> str:
        settings = _settings()
        if not settings.codex_subscription.enabled:
            raise typer.BadParameter("codex_subscription is not enabled")
        version = await CodexCliRunner(settings.codex_subscription).health_check()
        return json.dumps(
            {
                "provider": "codex-subscription",
                "status": "healthy",
                "version": version,
                "model": settings.codex_subscription.model,
            },
            indent=2,
        )

    typer.echo(_run_async(execute()))


@app.command("run")
def run_request(
    prompt: str = typer.Argument(..., help="Request for the coordinator."),
    session_id: UUID | None = typer.Option(None, help="Existing session; created when omitted."),
) -> None:
    """Submit a request and pause if human approval is required."""

    async def execute() -> str:
        settings = _settings()
        try:
            async with open_agent_runtime(settings, actor="cli") as runtime:
                result = await runtime.submit(prompt, session_id=session_id)
                return json.dumps(result.as_dict(), indent=2, default=str)
        except RecordNotFoundError as error:
            raise typer.BadParameter(str(error)) from error

    typer.echo(_run_async(execute()))


@app.command("chat")
def chat(
    session_id: UUID | None = typer.Option(
        None,
        help="Existing conversation session; created when omitted.",
    ),
) -> None:
    """Start an interactive, persistent, multi-turn terminal conversation."""

    async def execute() -> None:
        settings = _settings()
        try:
            async with open_agent_runtime(settings, actor="cli.chat") as runtime:
                await InteractiveChat(runtime, ConsoleTerminal()).run(
                    session_id=session_id
                )
        except RecordNotFoundError as error:
            raise typer.BadParameter(str(error)) from error

    try:
        _run_async(execute())
    except KeyboardInterrupt:
        typer.echo("Current operation cancelled.")


async def _resume_run(run_id: UUID, *, approved: bool) -> str:
    settings = _settings()
    try:
        async with open_agent_runtime(settings, actor="cli") as runtime:
            result = await runtime.resume(run_id, approved=approved)
            return json.dumps(result.as_dict(), indent=2, default=str)
    except RecordNotFoundError as error:
        raise typer.BadParameter(str(error)) from error


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
        try:
            async with open_agent_runtime(
                settings,
                actor="cli",
                initialize_agent=False,
            ) as runtime:
                inspection = await runtime.inspect(run_id)
                return json.dumps(inspection.as_dict(), indent=2)
        except RecordNotFoundError as error:
            raise typer.BadParameter(str(error)) from error

    typer.echo(_run_async(execute()))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
