"""Durable Telegram identity, session, and approval-token operations."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from personal_agent.persistence import Database
from personal_agent.persistence.models import SessionStatus, utc_now


class TelegramActionTokenError(ValueError):
    """Base error for an unusable Telegram approval token."""


class InvalidTelegramActionTokenError(TelegramActionTokenError):
    """The callback token does not exist or belongs to another identity."""


class ExpiredTelegramActionTokenError(TelegramActionTokenError):
    """The callback token has expired."""


class ConsumedTelegramActionTokenError(TelegramActionTokenError):
    """The callback token has already been used."""


@dataclass(frozen=True)
class TelegramActionAuthorization:
    """Validated decision recovered from an opaque callback token."""

    session_id: UUID
    run_id: UUID
    approved: bool


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class TelegramStateService:
    """Keep Telegram transport state durable without storing raw action tokens."""

    def __init__(
        self,
        *,
        database: Database,
        create_session: Callable[[], Awaitable[UUID]],
        actor: str,
    ) -> None:
        self._database = database
        self._create_session = create_session
        self._actor = actor

    async def claim_update(self, update_id: int) -> bool:
        async with self._database.unit_of_work() as unit_of_work:
            claimed = await unit_of_work.telegram.claim_update(update_id)
            if not claimed:
                return False
            await unit_of_work.audit.append(
                event_type="telegram.update_claimed",
                actor=self._actor,
                payload={"update_id": update_id},
            )
            await unit_of_work.commit()
        return True

    async def get_or_create_session(
        self,
        *,
        chat_id: int,
        user_id: int,
        force_new: bool = False,
    ) -> UUID:
        if not force_new:
            async with self._database.unit_of_work() as unit_of_work:
                binding = await unit_of_work.telegram.get_conversation(
                    chat_id=chat_id,
                    user_id=user_id,
                )
                if binding is not None:
                    session = await unit_of_work.sessions.get(UUID(binding.session_id))
                    if (
                        session is not None
                        and session.status == SessionStatus.ACTIVE.value
                        and _as_utc(session.expires_at) > utc_now()
                    ):
                        return UUID(binding.session_id)

        session_id = await self._create_session()
        async with self._database.unit_of_work() as unit_of_work:
            await unit_of_work.telegram.bind_conversation(
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
            )
            await unit_of_work.audit.append(
                event_type="telegram.conversation_bound",
                actor=self._actor,
                session_id=session_id,
                payload={"chat_id": chat_id, "user_id": user_id},
            )
            await unit_of_work.commit()
        return session_id

    async def create_action_token(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        chat_id: int,
        user_id: int,
        expires_at: datetime,
    ) -> str:
        token = secrets.token_urlsafe(18)
        token_digest = self._digest(token)
        async with self._database.unit_of_work() as unit_of_work:
            await unit_of_work.telegram.create_action_token(
                token_digest=token_digest,
                session_id=session_id,
                run_id=run_id,
                chat_id=chat_id,
                user_id=user_id,
                expires_at=_as_utc(expires_at),
            )
            await unit_of_work.audit.append(
                event_type="telegram.action_token_created",
                actor=self._actor,
                session_id=session_id,
                run_id=run_id,
                payload={
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "expires_at": _as_utc(expires_at).isoformat(),
                },
            )
            await unit_of_work.commit()
        return token

    async def consume_action_token(
        self,
        token: str,
        *,
        chat_id: int,
        user_id: int,
        approved: bool,
    ) -> TelegramActionAuthorization:
        token_digest = self._digest(token)
        async with self._database.unit_of_work() as unit_of_work:
            action = await unit_of_work.telegram.get_action_token(token_digest)
            if action is None or action.chat_id != chat_id or action.user_id != user_id:
                raise InvalidTelegramActionTokenError("approval action is invalid")
            if action.consumed_at is not None:
                raise ConsumedTelegramActionTokenError(
                    "approval action has already been used"
                )
            if _as_utc(action.expires_at) <= utc_now():
                raise ExpiredTelegramActionTokenError("approval action has expired")

            decision = "approved" if approved else "denied"
            consumed = await unit_of_work.telegram.consume_action_token(
                token_digest,
                decision=decision,
            )
            if not consumed:
                raise ConsumedTelegramActionTokenError(
                    "approval action has already been used"
                )
            session_id = UUID(action.session_id)
            run_id = UUID(action.run_id)
            await unit_of_work.audit.append(
                event_type="telegram.action_token_consumed",
                actor=self._actor,
                session_id=session_id,
                run_id=run_id,
                payload={
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "decision": decision,
                },
            )
            await unit_of_work.commit()
        return TelegramActionAuthorization(
            session_id=session_id,
            run_id=run_id,
            approved=approved,
        )

    async def audit_rejection(
        self,
        *,
        chat_id: int | None,
        user_id: int | None,
        reason: str,
    ) -> None:
        async with self._database.unit_of_work() as unit_of_work:
            await unit_of_work.audit.append(
                event_type="telegram.identity_rejected",
                actor=self._actor,
                payload={
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "reason": reason,
                },
            )
            await unit_of_work.commit()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
