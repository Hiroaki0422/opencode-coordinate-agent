"""Integration tests for the offline CLI commands."""

import json
from pathlib import Path

from typer.testing import CliRunner

from personal_agent.cli.app import app
from personal_agent.core.config import get_settings


def test_session_start_creates_durable_session(tmp_path: Path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    runner = CliRunner()
    get_settings.cache_clear()

    result = runner.invoke(
        app,
        ["session", "start"],
        env={
            "PERSONAL_AGENT_DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
            "PERSONAL_AGENT_DATA_DIR": str(tmp_path),
            "PERSONAL_AGENT_CHECKPOINT_PATH": str(tmp_path / "checkpoints.sqlite3"),
            "PERSONAL_AGENT_LOG_FORMAT": "json",
        },
    )
    get_settings.cache_clear()

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["session_id"]
    assert database_path.exists()


def test_cli_exposes_p0_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("session", "run", "approve", "deny", "inspect"):
        assert command in result.output
