"""Integration tests for durable Telegram transport state."""

import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from personal_agent.application.telegram import (
    ConsumedTelegramActionTokenError,
    TelegramStateService,
)
from personal_agent.persistence import Database
from personal_agent.persistence.models import TelegramActionTokenModel, utc_now


async def test_telegram_state_binds_sessions_claims_updates_and_consumes_token_once(
    database: Database,
) -> None:
    async def create_session():
        session_id = uuid4()
        async with database.unit_of_work() as unit_of_work:
            await unit_of_work.sessions.create(
                session_id=session_id,
                expires_at=utc_now() + timedelta(hours=1),
            )
            await unit_of_work.audit.append(
                event_type="session.created",
                actor="test.telegram",
                session_id=session_id,
            )
            await unit_of_work.commit()
        return session_id

    service = TelegramStateService(
        database=database,
        create_session=create_session,
        actor="test.telegram",
    )

    assert await service.claim_update(101) is True
    assert await service.claim_update(101) is False
    session_id = await service.get_or_create_session(chat_id=11, user_id=22)
    assert await service.get_or_create_session(chat_id=11, user_id=22) == session_id

    run_id = uuid4()
    async with database.unit_of_work() as unit_of_work:
        await unit_of_work.workflow_runs.create(session_id=session_id, run_id=run_id)
        await unit_of_work.audit.append(
            event_type="workflow.created",
            actor="test.telegram",
            session_id=session_id,
            run_id=run_id,
        )
        await unit_of_work.commit()

    token = await service.create_action_token(
        session_id=session_id,
        run_id=run_id,
        chat_id=11,
        user_id=22,
        expires_at=utc_now() + timedelta(minutes=5),
    )
    async with database.engine.connect() as connection:
        token_digest = (
            await connection.execute(select(TelegramActionTokenModel.token_digest))
        ).scalar_one()
    assert token_digest == hashlib.sha256(token.encode()).hexdigest()
    assert token not in token_digest

    authorization = await service.consume_action_token(
        token,
        chat_id=11,
        user_id=22,
        approved=True,
    )
    assert authorization.run_id == run_id
    assert authorization.approved is True
    with pytest.raises(ConsumedTelegramActionTokenError):
        await service.consume_action_token(
            token,
            chat_id=11,
            user_id=22,
            approved=False,
        )
