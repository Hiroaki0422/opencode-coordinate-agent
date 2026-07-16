"""Authenticated long-polling Telegram transport for the agent runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from personal_agent.application import (
    AgentRunResult,
    ConversationMessage,
    OperationReceiptInspection,
    SessionInspection,
    TelegramActionAuthorization,
    TelegramActionTokenError,
    render_operation_receipt,
)
from personal_agent.core.config import TelegramSettings
from personal_agent.observability import get_logger
from personal_agent.telegram.client import (
    TelegramApiError,
    TelegramBotClient,
    TelegramCallbackQuery,
    TelegramMessage,
    TelegramUpdate,
)


class TelegramState(Protocol):
    async def claim_update(self, update_id: int) -> bool: ...

    async def get_or_create_session(
        self,
        *,
        chat_id: int,
        user_id: int,
        force_new: bool = False,
    ) -> UUID: ...

    async def create_action_token(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        chat_id: int,
        user_id: int,
        expires_at: datetime,
    ) -> str: ...

    async def consume_action_token(
        self,
        token: str,
        *,
        chat_id: int,
        user_id: int,
        approved: bool,
    ) -> TelegramActionAuthorization: ...

    async def audit_rejection(
        self,
        *,
        chat_id: int | None,
        user_id: int | None,
        reason: str,
    ) -> None: ...


class TelegramRuntime(Protocol):
    @property
    def telegram(self) -> TelegramState: ...

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

    async def active_workspace(self, session_id: UUID) -> str | None: ...

    async def select_workspace(self, session_id: UUID, resource: str) -> str: ...

    async def list_workspaces(self, session_id: UUID) -> tuple[str, ...]: ...

    async def operation_receipt(
        self,
        session_id: UUID,
        run_id: UUID | None = None,
    ) -> OperationReceiptInspection | None: ...


class TelegramBot:
    """Translate allowlisted Telegram updates into runtime operations."""

    def __init__(
        self,
        *,
        settings: TelegramSettings,
        runtime: TelegramRuntime,
        client: TelegramBotClient,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._client = client
        self._sleep = sleep
        self._allowed_chat_ids = set(settings.allowed_chat_ids)
        self._allowed_user_ids = set(settings.allowed_user_ids)
        self._logger = get_logger(__name__)

    async def run(self) -> None:
        """Disable webhooks and poll forever until cancelled."""

        await self._client.delete_webhook()
        self._logger.info(
            "telegram.poll_started",
            allowed_chat_count=len(self._allowed_chat_ids),
            allowed_user_count=len(self._allowed_user_ids),
            poll_timeout_seconds=self._settings.poll_timeout_seconds,
        )
        offset: int | None = None
        while True:
            try:
                updates = await self._client.get_updates(
                    offset=offset,
                    timeout_seconds=self._settings.poll_timeout_seconds,
                )
            except TelegramApiError as error:
                self._logger.warning(
                    "telegram.poll_failed",
                    error_type=type(error).__name__,
                )
                await self._sleep(self._settings.retry_delay_seconds)
                continue

            if updates:
                self._logger.info(
                    "telegram.updates_received",
                    update_count=len(updates),
                    first_update_id=updates[0].update_id,
                    last_update_id=updates[-1].update_id,
                )
            for update in updates:
                offset = max(offset or 0, update.update_id + 1)
                try:
                    await self.process_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._logger.error(
                        "telegram.update_failed",
                        update_id=update.update_id,
                        error_type=type(error).__name__,
                    )
                    await self._notify_update_failure(update)

    async def process_update(self, update: TelegramUpdate) -> None:
        """Process one update once; exposed for deterministic offline tests."""

        if not await self._runtime.telegram.claim_update(update.update_id):
            self._logger.info(
                "telegram.update_skipped",
                update_id=update.update_id,
                reason="already_claimed",
            )
            return
        update_type = (
            "message"
            if update.message is not None
            else "callback_query"
            if update.callback_query is not None
            else "unsupported"
        )
        self._logger.info(
            "telegram.update_processing",
            update_id=update.update_id,
            update_type=update_type,
        )
        if update.message is not None:
            await self._handle_message(update.message)
            return
        if update.callback_query is not None:
            await self._handle_callback(update.callback_query)

    async def _handle_message(self, message: TelegramMessage) -> None:
        user_id = message.from_user.id if message.from_user is not None else None
        chat_id = message.chat.id
        if not await self._authorize(chat_id=chat_id, user_id=user_id):
            return
        if user_id is None:
            return
        text = (message.text or "").strip()
        if not text:
            await self._send_text(chat_id, "Send a text request or type /help.")
            return
        receipt_view = self._natural_receipt_request(text)
        if receipt_view is not None:
            session_id = await self._runtime.telegram.get_or_create_session(
                chat_id=chat_id,
                user_id=user_id,
            )
            receipt = await self._runtime.operation_receipt(session_id)
            await self._send_text(chat_id, render_operation_receipt(receipt, receipt_view))
            return
        if self._natural_created_file_request(text):
            session_id = await self._runtime.telegram.get_or_create_session(
                chat_id=chat_id,
                user_id=user_id,
            )
            receipt = await self._runtime.operation_receipt(session_id)
            changed = receipt.payload.get("changed_files", []) if receipt is not None else []
            if not isinstance(changed, list) or len(changed) != 1:
                choices = "\n".join(
                    f"{index}. {path}" for index, path in enumerate(changed, start=1)
                ) if isinstance(changed, list) else ""
                await self._send_text(
                    chat_id,
                    "I could not identify exactly one OpenCode-created file."
                    + (f" Choose one:\n{choices}" if choices else ""),
                )
                return
            text = (
                f"Read the file {changed[0]!r} from the current workspace and show its contents."
            )
        if text.startswith("/"):
            await self._handle_command(text, chat_id=chat_id, user_id=user_id)
            return

        session_id = await self._runtime.telegram.get_or_create_session(
            chat_id=chat_id,
            user_id=user_id,
        )
        status = await self._client.send_message(chat_id=chat_id, text="Planning…")
        await self._client.send_chat_action(chat_id=chat_id)
        try:
            result = await self._runtime.submit(text, session_id=session_id)
        except Exception:
            await self._replace_status(
                chat_id=chat_id,
                message_id=status.message_id,
                text="The request failed. Check WARN/ERROR logs on the agent host.",
            )
            raise
        await self._render_result(
            result,
            chat_id=chat_id,
            user_id=user_id,
            status_message_id=status.message_id,
        )

    async def _handle_command(self, text: str, *, chat_id: int, user_id: int) -> None:
        command = text.split(maxsplit=1)[0].casefold().split("@", maxsplit=1)[0]
        if command in {"/start", "/help"}:
            await self._send_text(
                chat_id,
                "Commands: /help /status /session /history /clear /new /workspace "
                "/workspaces /last-operation /operation\n"
                "Send any other text to talk to the agent.",
            )
            return
        session_id = await self._runtime.telegram.get_or_create_session(
            chat_id=chat_id,
            user_id=user_id,
            force_new=command == "/new",
        )
        if command == "/new":
            await self._send_text(chat_id, f"New session: {session_id}")
        elif command == "/session":
            await self._send_text(chat_id, str(session_id))
        elif command == "/status":
            status = await self._runtime.session_status(session_id)
            await self._send_text(chat_id, self._format_status(status))
        elif command == "/history":
            history = await self._runtime.conversation_history(session_id)
            await self._send_text(chat_id, self._format_history(history))
        elif command == "/clear":
            deleted = await self._runtime.clear_conversation_history(session_id)
            await self._send_text(chat_id, f"Cleared {deleted} conversation messages.")
        elif command == "/workspace":
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                workspace = await self._runtime.active_workspace(session_id)
                await self._send_text(
                    chat_id,
                    f"Active workspace: {workspace or 'none'}",
                )
            else:
                try:
                    workspace = await self._runtime.select_workspace(session_id, parts[1])
                except RuntimeError as error:
                    await self._send_text(chat_id, f"Workspace selection failed: {error}")
                else:
                    await self._send_text(chat_id, f"Active workspace: {workspace}")
        elif command == "/workspaces":
            try:
                workspaces = await self._runtime.list_workspaces(session_id)
            except RuntimeError as error:
                await self._send_text(chat_id, f"Workspace listing failed: {error}")
                return
            await self._send_text(
                chat_id,
                "Available workspaces:\n" + "\n".join(workspaces)
                if workspaces
                else "No configured workspaces exist.",
            )
        elif command in {"/last-operation", "/operation"}:
            parts = text.split()
            run_id: UUID | None = None
            view = "summary"
            if command == "/last-operation":
                if len(parts) >= 2:
                    view = parts[1].casefold()
            else:
                if len(parts) < 2:
                    await self._send_text(chat_id, "Usage: /operation <run-id> [log|diff|tests]")
                    return
                try:
                    run_id = UUID(parts[1])
                except ValueError:
                    await self._send_text(chat_id, "Operation run ID is invalid.")
                    return
                if len(parts) >= 3:
                    view = parts[2].casefold()
            try:
                rendered = render_operation_receipt(
                    await self._runtime.operation_receipt(session_id, run_id),
                    view,
                )
            except ValueError as error:
                rendered = str(error)
            await self._send_text(chat_id, rendered)
        else:
            await self._send_text(chat_id, f"Unknown command: {command}. Type /help.")

    async def _handle_callback(self, callback: TelegramCallbackQuery) -> None:
        message = callback.message
        chat_id = message.chat.id if message is not None else None
        user_id = callback.from_user.id
        if chat_id is None or not await self._authorize(chat_id=chat_id, user_id=user_id):
            await self._client.answer_callback_query(
                callback_query_id=callback.id,
                text="This action is not authorized.",
                show_alert=True,
            )
            return

        parsed = self._parse_callback(callback.data)
        if parsed is None:
            await self._client.answer_callback_query(
                callback_query_id=callback.id,
                text="This action is invalid.",
                show_alert=True,
            )
            return
        approved, token = parsed
        try:
            authorization = await self._runtime.telegram.consume_action_token(
                token,
                chat_id=chat_id,
                user_id=user_id,
                approved=approved,
            )
        except TelegramActionTokenError as error:
            await self._client.answer_callback_query(
                callback_query_id=callback.id,
                text=str(error),
                show_alert=True,
            )
            return

        decision = "Approved" if authorization.approved else "Denied"
        await self._client.answer_callback_query(
            callback_query_id=callback.id,
            text=decision,
        )
        if message is not None:
            await self._replace_status(
                chat_id=chat_id,
                message_id=message.message_id,
                text=f"{decision}. Executing…",
                reply_markup={"inline_keyboard": []},
            )
        try:
            result = await self._runtime.resume(
                authorization.run_id,
                approved=authorization.approved,
            )
        except Exception:
            await self._send_text(
                chat_id,
                "The approved run could not resume. Recover it from the host with:\n"
                f"personal-agent inspect {authorization.run_id}",
            )
            raise
        await self._render_result(
            result,
            chat_id=chat_id,
            user_id=user_id,
            status_message_id=message.message_id if message is not None else None,
        )

    async def _render_result(
        self,
        result: AgentRunResult,
        *,
        chat_id: int,
        user_id: int,
        status_message_id: int | None,
    ) -> None:
        if result.interrupts:
            approval = result.interrupts[0]
            if not isinstance(approval, dict):
                resumed = await self._runtime.resume(result.run_id, approved=False)
                await self._render_result(
                    resumed,
                    chat_id=chat_id,
                    user_id=user_id,
                    status_message_id=status_message_id,
                )
                return
            expires_at = self._parse_expiry(approval.get("expires_at"))
            if expires_at is None:
                resumed = await self._runtime.resume(result.run_id, approved=False)
                await self._render_result(
                    resumed,
                    chat_id=chat_id,
                    user_id=user_id,
                    status_message_id=status_message_id,
                )
                return
            token = await self._runtime.telegram.create_action_token(
                session_id=result.session_id,
                run_id=result.run_id,
                chat_id=chat_id,
                user_id=user_id,
                expires_at=expires_at,
            )
            approve_data = f"pa:a:{token}"
            deny_data = f"pa:d:{token}"
            if max(len(approve_data.encode()), len(deny_data.encode())) > 64:
                raise ValueError("Telegram callback data exceeds 64 bytes")
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "Approve", "callback_data": approve_data},
                        {"text": "Deny", "callback_data": deny_data},
                    ]
                ]
            }
            text = self._bounded(self._format_approval(approval, result.run_id))
            if status_message_id is None:
                await self._client.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                )
            else:
                await self._client.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=text,
                    reply_markup=keyboard,
                )
            return

        text = result.response or f"Status: {result.status or 'completed'}"
        if status_message_id is None:
            await self._send_text(chat_id, text)
        else:
            await self._replace_status(
                chat_id=chat_id,
                message_id=status_message_id,
                text=text,
            )

    async def _authorize(self, *, chat_id: int, user_id: int | None) -> bool:
        allowed = chat_id in self._allowed_chat_ids and user_id in self._allowed_user_ids
        if not allowed:
            self._logger.warning(
                "telegram.identity_rejected",
                chat_id=chat_id,
                user_id=user_id,
            )
            await self._runtime.telegram.audit_rejection(
                chat_id=chat_id,
                user_id=user_id,
                reason="identity_not_allowlisted",
            )
        return allowed

    async def _send_text(self, chat_id: int, text: str) -> None:
        for chunk in self._chunks(text):
            await self._client.send_message(chat_id=chat_id, text=chunk)

    async def _replace_status(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        chunks = self._chunks(text)
        await self._client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=chunks[0],
            reply_markup=reply_markup,
        )
        for chunk in chunks[1:]:
            await self._client.send_message(chat_id=chat_id, text=chunk)

    async def _notify_update_failure(self, update: TelegramUpdate) -> None:
        message = update.message
        if message is not None and message.chat.id in self._allowed_chat_ids:
            await self._send_text(
                message.chat.id,
                "The agent could not process this update. Check host WARN/ERROR logs.",
            )

    def _chunks(self, text: str) -> list[str]:
        content = text or "(empty response)"
        size = self._settings.max_message_chars
        return [content[index : index + size] for index in range(0, len(content), size)]

    def _bounded(self, text: str) -> str:
        if len(text) <= self._settings.max_message_chars:
            return text
        marker = "\n[approval details truncated]"
        return text[: self._settings.max_message_chars - len(marker)] + marker

    @staticmethod
    def _parse_callback(data: str | None) -> tuple[bool, str] | None:
        if not data:
            return None
        parts = data.split(":", maxsplit=2)
        if len(parts) != 3 or parts[0] != "pa" or parts[1] not in {"a", "d"}:
            return None
        if not parts[2]:
            return None
        return parts[1] == "a", parts[2]

    @staticmethod
    def _parse_expiry(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _format_approval(approval: dict[str, object], run_id: UUID) -> str:
        return "\n".join(
            [
                "Approval required",
                f"Run: {run_id}",
                f"Tool: {approval.get('tool_name')}",
                f"Operation: {approval.get('operation')}",
                f"Resource: {approval.get('resource')}",
                f"Effect: {approval.get('summary')}",
                f"Risk: {approval.get('risk_level')}",
                f"Reason: {approval.get('reason')}",
                f"Expires: {approval.get('expires_at')}",
            ]
        )

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
    def _format_history(history: Sequence[ConversationMessage]) -> str:
        if not history:
            return "No conversation history."
        return "\n\n".join(f"{message.role}> {message.content}" for message in history)

    @staticmethod
    def _natural_receipt_request(text: str) -> str | None:
        normalized = " ".join(text.casefold().split()).rstrip("?.!")
        if normalized in {
            "show the last opencode output",
            "show last opencode output",
            "show the opencode operation log",
            "show opencode operation log",
        }:
            return "log"
        if normalized in {"show the last operation", "show last operation"}:
            return "summary"
        return None

    @staticmethod
    def _natural_created_file_request(text: str) -> bool:
        normalized = " ".join(text.casefold().split()).rstrip("?.!")
        return normalized in {
            "show me the file opencode created",
            "show the file opencode created",
            "read the file opencode created",
        }
