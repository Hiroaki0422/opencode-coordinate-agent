"""Minimal versioned migrations for the v1 SQLite database."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from personal_agent.persistence.models import Base

Migration = Callable[[AsyncConnection], Awaitable[None]]


async def _migration_001_initial_schema(connection: AsyncConnection) -> None:
    await connection.run_sync(Base.metadata.create_all)
    await connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS audit_events_reject_update
        BEFORE UPDATE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit_events are append-only');
        END
        """
    )
    await connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS audit_events_reject_delete
        BEFORE DELETE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit_events are append-only');
        END
        """
    )


async def _migration_002_conversation_messages(connection: AsyncConnection) -> None:
    await connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id VARCHAR(36) NOT NULL UNIQUE,
            session_id VARCHAR(36) NOT NULL,
            run_id VARCHAR(36) NOT NULL,
            role VARCHAR(20) NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES workflow_runs (id) ON DELETE CASCADE
        )
        """
    )
    await connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_conversation_messages_session_id "
        "ON conversation_messages (session_id)"
    )
    await connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_conversation_messages_run_id "
        "ON conversation_messages (run_id)"
    )
    await connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_conversation_messages_role "
        "ON conversation_messages (role)"
    )
    await connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_conversation_messages_created_at "
        "ON conversation_messages (created_at)"
    )


async def _migration_003_telegram_transport(connection: AsyncConnection) -> None:
    await connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS telegram_conversations (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            session_id VARCHAR(36) NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (chat_id, user_id),
            FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
        )
        """
    )
    await connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_telegram_conversations_session_id "
        "ON telegram_conversations (session_id)"
    )
    await connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS telegram_action_tokens (
            token_digest VARCHAR(64) PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            run_id VARCHAR(36) NOT NULL,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            expires_at DATETIME NOT NULL,
            consumed_at DATETIME,
            decision VARCHAR(20),
            FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES workflow_runs (id) ON DELETE CASCADE
        )
        """
    )
    for column in ("session_id", "run_id", "chat_id", "user_id", "expires_at"):
        await connection.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS ix_telegram_action_tokens_{column} "
            f"ON telegram_action_tokens ({column})"
        )
    await connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS telegram_updates (
            update_id INTEGER PRIMARY KEY,
            claimed_at DATETIME NOT NULL
        )
        """
    )


async def _migration_004_workspace_and_operation_receipts(
    connection: AsyncConnection,
) -> None:
    await connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS session_workspaces (
            session_id VARCHAR(36) PRIMARY KEY,
            active_workspace TEXT NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
        )
        """
    )
    await connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS tool_operation_receipts (
            id VARCHAR(36) PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            run_id VARCHAR(36) NOT NULL UNIQUE,
            action_id VARCHAR(36) NOT NULL,
            audit_event_id VARCHAR(36) NOT NULL,
            tool_name VARCHAR(100) NOT NULL,
            operation VARCHAR(100) NOT NULL,
            resource TEXT NOT NULL,
            success BOOLEAN NOT NULL,
            outcome VARCHAR(20) NOT NULL,
            payload JSON NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY(audit_event_id) REFERENCES audit_events (id) ON DELETE RESTRICT
        )
        """
    )
    for column in (
        "session_id",
        "run_id",
        "action_id",
        "tool_name",
        "outcome",
        "created_at",
    ):
        await connection.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS ix_tool_operation_receipts_{column} "
            f"ON tool_operation_receipts ({column})"
        )


MIGRATIONS: tuple[tuple[int, str, Migration], ...] = (
    (1, "initial persistence schema", _migration_001_initial_schema),
    (2, "conversation message storage", _migration_002_conversation_messages),
    (3, "telegram transport state", _migration_003_telegram_transport),
    (4, "active workspace and operation receipts", _migration_004_workspace_and_operation_receipts),
)


async def apply_migrations(engine: AsyncEngine) -> None:
    """Apply each unapplied migration once, in order."""

    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        result = await connection.execute(text("SELECT version FROM schema_migrations"))
        applied_versions = set(result.scalars())

        for version, description, migration in MIGRATIONS:
            if version in applied_versions:
                continue
            await migration(connection)
            await connection.execute(
                text(
                    """
                    INSERT INTO schema_migrations (version, description, applied_at)
                    VALUES (:version, :description, :applied_at)
                    """
                ),
                {
                    "version": version,
                    "description": description,
                    "applied_at": datetime.now(UTC).isoformat(),
                },
            )
