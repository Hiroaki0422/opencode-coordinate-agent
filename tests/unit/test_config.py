"""Tests for startup configuration validation."""

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from personal_agent.core.config import (
    CoordinatorSettings,
    DeepSeekSettings,
    Environment,
    ModelTarget,
    OpenAISettings,
    SandboxBackend,
    Settings,
)


class SettingsWithoutDotEnv(Settings):
    """Load process environment variables without reading a developer's .env file."""

    model_config = SettingsConfigDict(
        env_prefix="PERSONAL_AGENT_",
        env_nested_delimiter="__",
        extra="ignore",
    )


@pytest.fixture(autouse=True)
def clear_agent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent host configuration from affecting settings tests."""

    for name in (
        "PERSONAL_AGENT_ENVIRONMENT",
        "PERSONAL_AGENT_DATABASE_URL",
        "PERSONAL_AGENT_DATA_DIR",
        "PERSONAL_AGENT_CHECKPOINT_PATH",
        "PERSONAL_AGENT_POLICY_PATH",
        "PERSONAL_AGENT_LOG_LEVEL",
        "PERSONAL_AGENT_LOG_FORMAT",
        "PERSONAL_AGENT_LOG_REDACTED_FIELDS",
        "PERSONAL_AGENT_OPENAI__ENABLED",
        "PERSONAL_AGENT_OPENAI__API_KEY",
        "PERSONAL_AGENT_DEEPSEEK__ENABLED",
        "PERSONAL_AGENT_DEEPSEEK__API_KEY",
        "PERSONAL_AGENT_COORDINATOR__ENABLED",
        "PERSONAL_AGENT_COORDINATOR__MODELS",
        "PERSONAL_AGENT_TODOIST__ENABLED",
        "PERSONAL_AGENT_TODOIST__API_TOKEN",
        "PERSONAL_AGENT_TODOIST__BASE_URL",
        "PERSONAL_AGENT_TODOIST__TIMEOUT_SECONDS",
        "PERSONAL_AGENT_RESEARCH__ENABLED",
        "PERSONAL_AGENT_RESEARCH__PROVIDERS",
        "PERSONAL_AGENT_RESEARCH__MAX_RESULTS",
        "PERSONAL_AGENT_RESEARCH__REGION",
        "PERSONAL_AGENT_RESEARCH__SAFE_SEARCH",
        "PERSONAL_AGENT_RESEARCH__SEARCH_TIMEOUT_SECONDS",
        "PERSONAL_AGENT_RESEARCH__FETCH_TIMEOUT_SECONDS",
        "PERSONAL_AGENT_RESEARCH__MAX_PAGE_BYTES",
        "PERSONAL_AGENT_RESEARCH__MAX_CONTENT_CHARS",
        "PERSONAL_AGENT_TELEGRAM__ENABLED",
        "PERSONAL_AGENT_TELEGRAM__BOT_TOKEN",
        "PERSONAL_AGENT_TELEGRAM__ALLOWED_CHAT_IDS",
        "PERSONAL_AGENT_LOCAL_EXECUTION__ENABLED",
        "PERSONAL_AGENT_LOCAL_EXECUTION__WORKSPACE_ROOT",
        "PERSONAL_AGENT_LOCAL_EXECUTION__SANDBOX_BACKEND",
        "PERSONAL_AGENT_LOCAL_EXECUTION__DOCKER_IMAGE",
        "PERSONAL_AGENT_LOCAL_EXECUTION__COMMAND_TIMEOUT_SECONDS",
        "PERSONAL_AGENT_LOCAL_EXECUTION__MAX_OUTPUT_BYTES",
        "PERSONAL_AGENT_LOCAL_EXECUTION__MEMORY_LIMIT",
        "PERSONAL_AGENT_LOCAL_EXECUTION__CPU_LIMIT",
        "PERSONAL_AGENT_LOCAL_EXECUTION__PIDS_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_leave_optional_integrations_disabled() -> None:
    settings = SettingsWithoutDotEnv()

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.openai.enabled is False
    assert settings.todoist.enabled is False
    assert settings.telegram.enabled is False
    workspace_root = settings.local_execution.workspace_root
    assert workspace_root == workspace_root.expanduser()
    assert settings.local_execution.sandbox_backend is SandboxBackend.DOCKER


@pytest.mark.parametrize(
    ("enabled_variable", "missing_secret", "expected_message"),
    [
        ("PERSONAL_AGENT_OPENAI__ENABLED", "PERSONAL_AGENT_OPENAI__API_KEY", "openai.api_key"),
        (
            "PERSONAL_AGENT_TODOIST__ENABLED",
            "PERSONAL_AGENT_TODOIST__API_TOKEN",
            "todoist.api_token",
        ),
        (
            "PERSONAL_AGENT_DEEPSEEK__ENABLED",
            "PERSONAL_AGENT_DEEPSEEK__API_KEY",
            "deepseek.api_key",
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
        SettingsWithoutDotEnv()


def test_telegram_requires_a_token_and_allowlisted_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_TELEGRAM__ENABLED", "true")
    monkeypatch.setenv("PERSONAL_AGENT_TELEGRAM__BOT_TOKEN", "telegram-secret")

    with pytest.raises(ValidationError, match="telegram.allowed_chat_ids"):
        SettingsWithoutDotEnv()


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
    monkeypatch.setenv("PERSONAL_AGENT_LOCAL_EXECUTION__ENABLED", "true")
    monkeypatch.setenv("PERSONAL_AGENT_LOCAL_EXECUTION__WORKSPACE_ROOT", "~/agent-workspaces")

    settings = SettingsWithoutDotEnv()

    assert settings.environment is Environment.PRODUCTION
    assert settings.openai.api_key is not None
    assert settings.openai.api_key.get_secret_value() == "openai-secret"
    assert settings.todoist.api_token is not None
    assert settings.todoist.api_token.get_secret_value() == "todoist-secret"
    assert settings.telegram.allowed_chat_ids == [123456]
    assert settings.local_execution.enabled is True
    assert settings.local_execution.workspace_root.name == "agent-workspaces"
    assert settings.local_execution.sandbox_backend is SandboxBackend.DOCKER


def test_coordinator_requires_models_and_enabled_builtin_providers() -> None:
    with pytest.raises(ValidationError, match="coordinator.models"):
        SettingsWithoutDotEnv(coordinator=CoordinatorSettings(enabled=True))

    with pytest.raises(ValidationError, match="openai must be enabled"):
        SettingsWithoutDotEnv(
            coordinator=CoordinatorSettings(
                enabled=True,
                models=[ModelTarget(provider="openai", model="test-model")],
            )
        )

    settings = SettingsWithoutDotEnv(
        openai=OpenAISettings(enabled=True, api_key=SecretStr("secret")),
        deepseek=DeepSeekSettings(enabled=True, api_key=SecretStr("secret")),
        coordinator=CoordinatorSettings(
            enabled=True,
            models=[
                ModelTarget(provider="openai", model="primary"),
                ModelTarget(provider="deepseek", model="fallback"),
            ],
        ),
    )
    assert [target.provider for target in settings.coordinator.models] == [
        "openai",
        "deepseek",
    ]
