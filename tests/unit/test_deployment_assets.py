"""Offline validation for single-host deployment assets."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIRECTORY = REPOSITORY_ROOT / "deploy"
SYSTEMD_DIRECTORY = DEPLOY_DIRECTORY / "systemd"
SCRIPTS = (
    DEPLOY_DIRECTORY / "install-systemd.sh",
    DEPLOY_DIRECTORY / "backup-state.sh",
    DEPLOY_DIRECTORY / "restore-state.sh",
    DEPLOY_DIRECTORY / "upgrade-systemd.sh",
)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_deployment_scripts_are_executable_and_valid_bash(script: Path) -> None:
    assert script.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_systemd_template_runs_one_hardened_telegram_process() -> None:
    unit = (SYSTEMD_DIRECTORY / "personal-agent-telegram.service.in").read_text()

    assert "ExecStart=@APP_DIR@/.venv/bin/personal-agent telegram" in unit
    assert "EnvironmentFile=@ENV_FILE@" in unit
    assert "User=@SERVICE_USER@" in unit
    assert "SupplementaryGroups=docker" in unit
    assert "test -r @ENV_FILE@" not in unit
    assert "Restart=on-failure" in unit
    assert "KillSignal=SIGINT" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=true" in unit
    assert "ReadWritePaths=@STATE_DIR@" in unit
    assert "CapabilityBoundingSet=" in unit


def test_production_environment_uses_absolute_state_paths_and_no_secret() -> None:
    environment = (SYSTEMD_DIRECTORY / "personal-agent.env.example").read_text()

    assert "PERSONAL_AGENT_ENVIRONMENT=production" in environment
    assert (
        "PERSONAL_AGENT_DATABASE_URL="
        "sqlite+aiosqlite:////var/lib/personal-agent/data/personal_agent.sqlite3"
    ) in environment
    assert "PERSONAL_AGENT_CHECKPOINT_PATH=/var/lib/personal-agent/data/" in environment
    assert "PERSONAL_AGENT_LOCAL_EXECUTION__WORKSPACE_ROOT=/var/lib/" in environment
    assert "PERSONAL_AGENT_TELEGRAM__BOT_TOKEN=\n" in environment
    assert "replace-me" not in environment
    assert "@APP_DIR@" not in environment


def test_installer_renders_every_systemd_placeholder() -> None:
    template = (SYSTEMD_DIRECTORY / "personal-agent-telegram.service.in").read_text()
    installer = (DEPLOY_DIRECTORY / "install-systemd.sh").read_text()
    rendered = (
        template.replace("@SERVICE_USER@", "personal-agent")
        .replace("@APP_DIR@", "/opt/personal-agent")
        .replace("@STATE_DIR@", "/var/lib/personal-agent")
        .replace("@ENV_FILE@", "/etc/personal-agent/personal-agent.env")
    )

    assert "@" not in rendered
    assert "WorkingDirectory=/opt/personal-agent" in rendered
    assert "ReadWritePaths=/var/lib/personal-agent" in rendered
    assert "/root/.local/bin/uv" in installer
    assert 'UV_BIN="${UV_BIN:-$(command -v uv || true)}"' in installer
    assert 'UV_PYTHON_INSTALL_DIR="$PYTHON_INSTALL_DIR"' in installer
    assert '"$UV_BIN" venv --clear --managed-python --python 3.12 .venv' in installer


def test_backup_restore_and_upgrade_are_fail_closed() -> None:
    backup = (DEPLOY_DIRECTORY / "backup-state.sh").read_text()
    restore = (DEPLOY_DIRECTORY / "restore-state.sh").read_text()
    upgrade = (DEPLOY_DIRECTORY / "upgrade-systemd.sh").read_text()

    assert "systemctl stop" in backup
    assert "sha256sum" in backup
    assert "--exclude='./codex-auth'" in backup
    assert "sha256sum --check" in restore
    assert "PRAGMA integrity_check" in restore
    assert "archive contains an unsafe path" in restore
    assert "pre-restore" in restore
    assert "merge --ff-only" in upgrade
    assert "backup-state.sh\" --leave-stopped" in upgrade
    assert "git reset" not in upgrade


def test_deployment_document_covers_required_operations() -> None:
    guide = (REPOSITORY_ROOT / "docs" / "deployment.md").read_text()

    for heading in (
        "## Install",
        "## Start and verify",
        "## Back up",
        "## Restore",
        "## Upgrade",
        "## Roll back",
        "## Rotate secrets",
        "## Troubleshooting",
    ):
        assert heading in guide
