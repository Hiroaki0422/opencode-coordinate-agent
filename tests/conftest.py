"""Shared offline test fixtures."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from personal_agent.persistence import Database


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    database_path = tmp_path / "personal_agent.sqlite3"
    instance = Database(f"sqlite+aiosqlite:///{database_path}")
    await instance.initialize()
    yield instance
    await instance.dispose()
