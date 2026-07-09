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


MIGRATIONS: tuple[tuple[int, str, Migration], ...] = (
    (1, "initial persistence schema", _migration_001_initial_schema),
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
