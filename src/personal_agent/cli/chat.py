"""Interactive terminal transport for durable multi-turn conversations."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

import typer

from personal_agent.application import (
    AgentRunResult,
    ConversationMessage,
    SessionInspection,
)


class ChatRuntime(Protocol):
    async def create_session(self) -> UUID: ...

    async def submit(
        self,
        prompt: str,
        *,
        session_id: UUID | None = None,
    ) -> AgentRunResult: ...

    async def resume(self, run_id: UUID, *, approved: bool) -> AgentRunResult: ...

    async def session_status(self, session_id: UUID) -> SessionInspection: ...

    async def conversation_history(
        self,
        session_id: UUID,
    ) -> tuple[ConversationMessage, ...]: ...

    async def clear_conversation_history(self, session_id: UUID) -> int: ...


class Terminal(Protocol):
    async def read(self, prompt: str) -> str: ...

    def write(self, message: str = "") -> None: ...


class ConsoleTerminal:
    async def read(self, prompt: str) -> str:
        return await asyncio.to_thread(input, prompt)

    def write(self, message: str = "") -> None:
        typer.echo(message)


class InteractiveChat:
    """Run one interactive conversation without owning application resources."""

    def __init__(self, runtime: ChatRuntime, terminal: Terminal) -> None:
        self._runtime = runtime
        self._terminal = terminal

    async def run(self, *, session_id: UUID | None = None) -> None:
        active_session_id = await self._start_session(session_id)
        self._terminal.write(f"Session: {active_session_id}")
        self._terminal.write("Type /help for commands or /quit to exit.")

        while True:
            try:
                user_input = (await self._terminal.read("you> ")).strip()
            except EOFError:
                self._terminal.write("Exiting.")
                return
            except KeyboardInterrupt:
                self._terminal.write("Input cancelled. Type /quit to exit.")
                continue

            if not user_input:
                continue
            if user_input.startswith("/"):
                should_exit, active_session_id = await self._handle_command(
                    user_input,
                    active_session_id,
                )
                if should_exit:
                    return
                continue

            try:
                result = await self._runtime.submit(
                    user_input,
                    session_id=active_session_id,
                )
                await self._render_result(result)
            except KeyboardInterrupt:
                self._terminal.write("Current operation cancelled.")

    async def _start_session(self, session_id: UUID | None) -> UUID:
        if session_id is None:
            return await self._runtime.create_session()
        await self._runtime.session_status(session_id)
        return session_id

    async def _handle_command(
        self,
        command_line: str,
        session_id: UUID,
    ) -> tuple[bool, UUID]:
        command = command_line.casefold()
        if command in {"/quit", "/exit"}:
            self._terminal.write("Exiting.")
            return True, session_id
        if command == "/help":
            self._terminal.write(
                "Commands: /help /status /session /history /clear /new /quit"
            )
            return False, session_id
        if command == "/session":
            self._terminal.write(str(session_id))
            return False, session_id
        if command == "/status":
            status = await self._runtime.session_status(session_id)
            self._terminal.write(self._format_status(status))
            return False, session_id
        if command == "/history":
            history = await self._runtime.conversation_history(session_id)
            self._render_history(history)
            return False, session_id
        if command == "/clear":
            deleted = await self._runtime.clear_conversation_history(session_id)
            self._terminal.write(f"Cleared {deleted} conversation messages.")
            return False, session_id
        if command == "/new":
            new_session_id = await self._runtime.create_session()
            self._terminal.write(f"New session: {new_session_id}")
            return False, new_session_id
        self._terminal.write(f"Unknown command: {command_line}. Type /help.")
        return False, session_id

    async def _render_result(self, result: AgentRunResult) -> None:
        active_result = result
        while active_result.interrupts:
            approval = active_result.interrupts[0]
            self._terminal.write(f"Paused run: {active_result.run_id}")
            if not isinstance(approval, dict):
                self._terminal.write("Approval request was malformed; denying it.")
                approved = False
            else:
                self._terminal.write(self._format_approval(approval))
                approved = await self._read_approval()
            active_result = await self._runtime.resume(
                active_result.run_id,
                approved=approved,
            )

        if active_result.response:
            self._terminal.write(f"assistant> {active_result.response}")
        elif active_result.status:
            self._terminal.write(f"Status: {active_result.status}")

    async def _read_approval(self) -> bool:
        while True:
            try:
                response = (await self._terminal.read("approval [approve/deny]> ")).strip()
            except (EOFError, KeyboardInterrupt):
                self._terminal.write("Approval denied.")
                return False
            normalized = response.casefold()
            if normalized in {"approve", "yes", "y"}:
                return True
            if normalized in {"deny", "no", "n", ""}:
                return False
            self._terminal.write("Enter `approve` or `deny`; empty input denies.")

    def _render_history(self, history: Sequence[ConversationMessage]) -> None:
        if not history:
            self._terminal.write("No conversation history.")
            return
        for message in history:
            self._terminal.write(f"{message.role}> {message.content}")

    @staticmethod
    def _format_status(status: SessionInspection) -> str:
        latest = (
            f"{status.latest_run_id} ({status.latest_run_status})"
            if status.latest_run_id is not None
            else "none"
        )
        return (
            f"Session {status.session_id}: {status.status}; "
            f"expires {status.expires_at.isoformat()}; latest run: {latest}"
        )

    @staticmethod
    def _format_approval(approval: dict[str, object]) -> str:
        return "\n".join(
            [
                "Approval required:",
                f"  Tool: {approval.get('tool_name')}",
                f"  Operation: {approval.get('operation')}",
                f"  Resource: {approval.get('resource')}",
                f"  Effect: {approval.get('summary')}",
                f"  Risk: {approval.get('risk_level')}",
                f"  Reason: {approval.get('reason')}",
                f"  Expires: {approval.get('expires_at')}",
            ]
        )
