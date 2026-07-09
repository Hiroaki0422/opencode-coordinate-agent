"""Async SQLite database lifecycle."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from personal_agent.persistence.migrations import apply_migrations
from personal_agent.persistence.repositories import UnitOfWork


class Database:
    """Own the SQLAlchemy engine, migrations, and transaction factory."""

    def __init__(self, database_url: str) -> None:
        if not database_url.startswith("sqlite+aiosqlite://"):
            raise ValueError("v1 supports only sqlite+aiosqlite database URLs")

        self.engine: AsyncEngine = create_async_engine(database_url)
        self._session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure_sqlite(dbapi_connection: Any, connection_record: Any) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    async def initialize(self) -> None:
        """Create or migrate the configured database."""

        await apply_migrations(self.engine)

    def unit_of_work(self) -> UnitOfWork:
        """Create an isolated audited transaction."""

        return UnitOfWork(self._session_factory)

    async def dispose(self) -> None:
        """Release all pooled database connections."""

        await self.engine.dispose()
