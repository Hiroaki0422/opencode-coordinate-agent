"""Tests for startup configuration validation."""

import pytest
from pydantic import ValidationError

from personal_agent.core.config import Environment, Settings


@pytest.fixture(autouse=True)
def clear_agent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent host configuration from affecting settings tests."""

    for name in (
        "PERSONAL_AGENT_ENVIRONMENT",
        "PERSONAL_AGENT_DATABASE_URL",
        "PERSONAL_AGENT_DATA_DIR",
        "PERSONAL_AGENT_POLICY_PATH",
        "PERSONAL_AGENT_LOG_LEVEL",
        "PERSONAL_AGENT_OPENAI__ENABLED",
        "PERSONAL_AGENT_OPENAI__API_KEY",
        "PERSONAL_AGENT_TODOIST__ENABLED",
        "PERSONAL_AGENT_TODOIST__API_TOKEN",
        "PERSONAL_AGENT_TELEGRAM__ENABLED",
        "PERSONAL_AGENT_TELEGRAM__BOT_TOKEN",
        "PERSONAL_AGENT_TELEGRAM__ALLOWED_CHAT_IDS",
        "PERSONAL_AGENT_MAC_WORKER__ENABLED",
        "PERSONAL_AGENT_MAC_WORKER__SHARED_SECRET",
        "PERSONAL_AGENT_MAC_WORKER__WORKSPACE_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_leave_optional_integrations_disabled() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.openai.enabled is False
    assert settings.todoist.enabled is False
    assert settings.telegram.enabled is False
    assert settings.mac_worker.workspace_root == settings.mac_worker.workspace_root.expanduser()


@pytest.mark.parametrize(
    ("enabled_variable", "missing_secret", "expected_message"),
    [
        ("PERSONAL_AGENT_OPENAI__ENABLED", "PERSONAL_AGENT_OPENAI__API_KEY", "openai.api_key"),
        ("PERSONAL_AGENT_TODOIST__ENABLED", "PERSONAL_AGENT_TODOIST__API_TOKEN", "todoist.api_token"),
        (
            "PERSONAL_AGENT_MAC_WORKER__ENABLED",
            "PERSONAL_AGENT_MAC_WORKER__SHARED_SECRET",
            "mac_worker.shared_secret",
        ),
    ],
)
def test_enabled_integrations_require_their_secret(
    monkeypatch: pytest.MonkeyPatch,
    enabled_variable: str,
    missing_secret: str,
    expected_message: str,
) -> None:
    monkeypatch.setenv(enabled_variable, "true")
    monkeypatch.delenv(missing_secret, raising=False)

    with pytest.raises(ValidationError, match=expected_message):
        Settings(_env_file=None)


def test_telegram_requires_a_token_and_allowlisted_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_TELEGRAM__ENABLED", "true")
    monkeypatch.setenv("PERSONAL_AGENT_TELEGRAM__BOT_TOKEN", "telegram-secret")

    with pytest.raises(ValidationError, match="telegram.allowed_chat_ids"):
        Settings(_env_file=None)


def test_enabled_integrations_load_from_nested_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_ENVIRONMENT", "production")
    monkeypatch.setenv("PERSONAL_AGENT_OPENAI__ENABLED", "true")
    monkeypatch.setenv("PERSONAL_AGENT_OPENAI__API_KEY", "openai-secret")
    monkeypatch.setenv("PERSONAL_AGENT_TODOIST__ENABLED", "true")
    monkeypatch.setenv("PERSONAL_AGENT_TODOIST__API_TOKEN", "todoist-secret")
    monkeypatch.setenv("PERSONAL_AGENT_TELEGRAM__ENABLED", "true")
    monkeypatch.setenv("PERSONAL_AGENT_TELEGRAM__BOT_TOKEN", "telegram-secret")
    monkeypatch.setenv("PERSONAL_AGENT_TELEGRAM__ALLOWED_CHAT_IDS", "[123456]")
    monkeypatch.setenv("PERSONAL_AGENT_MAC_WORKER__ENABLED", "true")
    monkeypatch.setenv("PERSONAL_AGENT_MAC_WORKER__SHARED_SECRET", "worker-secret")

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.PRODUCTION
    assert settings.openai.api_key is not None
    assert settings.openai.api_key.get_secret_value() == "openai-secret"
    assert settings.todoist.api_token is not None
    assert settings.todoist.api_token.get_secret_value() == "todoist-secret"
    assert settings.telegram.allowed_chat_ids == [123456]
    assert settings.mac_worker.shared_secret is not None
    assert settings.mac_worker.shared_secret.get_secret_value() == "worker-secret"
