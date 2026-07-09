"""Application settings and startup-time configuration validation."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported deployment environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class OpenAISettings(BaseModel):
    """Configuration for the coordinator and research model provider."""

    enabled: bool = False
    api_key: SecretStr | None = None


class TodoistSettings(BaseModel):
    """Configuration for the Todoist task-provider adapter."""

    enabled: bool = False
    api_token: SecretStr | None = None


class TelegramSettings(BaseModel):
    """Configuration for the Telegram transport."""

    enabled: bool = False
    bot_token: SecretStr | None = None
    allowed_chat_ids: list[int] = Field(default_factory=list)


class LocalExecutionSettings(BaseModel):
    """Configuration for shell and coding tools on the current host."""

    enabled: bool = False
    workspace_root: Path = Field(default=Path("~/agent-workspaces"), validate_default=True)

    @field_validator("workspace_root", mode="after")
    @classmethod
    def expand_workspace_root(cls, value: Path) -> Path:
        """Normalize the workspace root before policy checks use it."""

        return value.expanduser()


class Settings(BaseSettings):
    """Configuration loaded from the environment and an optional local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PERSONAL_AGENT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    database_url: str = "sqlite+aiosqlite:///./data/personal_agent.sqlite3"
    data_dir: Path = Path("data")
    policy_path: Path = Path("config/policy.yaml")
    log_level: str = "INFO"
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    todoist: TodoistSettings = Field(default_factory=TodoistSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    local_execution: LocalExecutionSettings = Field(default_factory=LocalExecutionSettings)

    @field_validator("data_dir", "policy_path", mode="after")
    @classmethod
    def expand_paths(cls, value: Path) -> Path:
        """Allow user-relative locations without resolving non-existent paths."""

        return value.expanduser()

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """Reject invalid standard-library log levels before startup."""

        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            message = "log_level must be CRITICAL, ERROR, WARNING, INFO, or DEBUG"
            raise ValueError(message)
        return normalized

    @model_validator(mode="after")
    def validate_enabled_integrations(self) -> Self:
        """Require credentials and identity scope for enabled capabilities."""

        if self.openai.enabled and self.openai.api_key is None:
            raise ValueError("openai.api_key is required when openai.enabled is true")
        if self.todoist.enabled and self.todoist.api_token is None:
            raise ValueError("todoist.api_token is required when todoist.enabled is true")
        if self.telegram.enabled:
            if self.telegram.bot_token is None:
                raise ValueError("telegram.bot_token is required when telegram.enabled is true")
            if not self.telegram.allowed_chat_ids:
                raise ValueError(
                    "telegram.allowed_chat_ids is required when telegram.enabled is true"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Load and cache process-wide application settings."""

    return Settings()
