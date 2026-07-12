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


class SandboxBackend(StrEnum):
    """Supported isolation backends for host-local execution."""

    DOCKER = "docker"


class LogFormat(StrEnum):
    """Supported application log renderers."""

    CONSOLE = "console"
    JSON = "json"


class OpenAISettings(BaseModel):
    """Configuration for the coordinator and research model provider."""

    enabled: bool = False
    api_key: SecretStr | None = None


class DeepSeekSettings(BaseModel):
    """Configuration for DeepSeek-backed model targets."""

    enabled: bool = False
    api_key: SecretStr | None = None


class ModelTarget(BaseModel):
    """One ordered model candidate in a provider-neutral fallback route."""

    provider: str
    model: str


class CoordinatorSettings(BaseModel):
    """Configuration for the main planning and response agent."""

    enabled: bool = False
    models: list[ModelTarget] = Field(default_factory=list)


class TodoistSettings(BaseModel):
    """Configuration for the Todoist task-provider adapter."""

    enabled: bool = False
    api_token: SecretStr | None = None
    base_url: str = "https://api.todoist.com/api/v1"
    timeout_seconds: float = Field(default=15.0, gt=0)


class ResearchSettings(BaseModel):
    """Configuration for provider-neutral web research."""

    enabled: bool = True
    providers: list[str] = Field(default_factory=lambda: ["duckduckgo"])
    max_results: int = Field(default=5, ge=1, le=10)
    region: str = "us-en"
    safe_search: str = "moderate"
    search_timeout_seconds: float = Field(default=10.0, gt=0)
    fetch_timeout_seconds: float = Field(default=15.0, gt=0)
    max_page_bytes: int = Field(default=1_000_000, ge=1_024)
    max_content_chars: int = Field(default=12_000, ge=500)


class TelegramSettings(BaseModel):
    """Configuration for the Telegram transport."""

    enabled: bool = False
    bot_token: SecretStr | None = None
    allowed_chat_ids: list[int] = Field(default_factory=list)


class PolicySettings(BaseModel):
    """Durations for sessions and human approval decisions."""

    session_ttl_minutes: int = Field(default=120, gt=0)
    approval_ttl_minutes: int = Field(default=15, gt=0)


class LocalExecutionSettings(BaseModel):
    """Configuration for shell and coding tools on the current host."""

    enabled: bool = False
    workspace_root: Path = Field(default=Path("~/agent-workspaces"), validate_default=True)
    sandbox_backend: SandboxBackend = SandboxBackend.DOCKER
    docker_image: str = "personal-agent-sandbox:latest"
    command_timeout_seconds: float = Field(default=120.0, gt=0)
    max_output_bytes: int = Field(default=1_000_000, ge=1_024)
    memory_limit: str = "512m"
    cpu_limit: float = Field(default=1.0, gt=0)
    pids_limit: int = Field(default=128, ge=16)
    repository_paths: list[Path] = Field(default_factory=list)

    @field_validator("workspace_root", mode="after")
    @classmethod
    def expand_workspace_root(cls, value: Path) -> Path:
        """Normalize the workspace root before policy checks use it."""

        return value.expanduser()

    @field_validator("repository_paths", mode="after")
    @classmethod
    def expand_repository_paths(cls, values: list[Path]) -> list[Path]:
        """Normalize explicitly allowlisted existing repositories."""

        return [value.expanduser() for value in values]


class OpenCodeSettings(BaseModel):
    """Configuration for sandboxed coding delegation."""

    enabled: bool = False
    executable: str = "opencode"
    model: str = "deepseek/deepseek-chat"
    timeout_seconds: float = Field(default=900.0, gt=0)
    max_diff_chars: int = Field(default=20_000, ge=1_000)
    max_report_chars: int = Field(default=8_000, ge=500)


class CodexSubscriptionSettings(BaseModel):
    """Configuration for ChatGPT subscription access through Codex CLI."""

    enabled: bool = False
    executable: str = "codex"
    model: str = "gpt-5.4"
    timeout_seconds: float = Field(default=120.0, gt=0)
    working_directory: Path = Field(
        default=Path("/tmp/personal-agent-codex"),
        validate_default=True,
    )
    max_prompt_chars: int = Field(default=80_000, ge=1_000)
    max_response_bytes: int = Field(default=256_000, ge=1_024)
    max_stderr_chars: int = Field(default=2_000, ge=256)
    corrective_retries: int = Field(default=1, ge=0, le=2)

    @field_validator("working_directory", mode="after")
    @classmethod
    def expand_working_directory(cls, value: Path) -> Path:
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
    checkpoint_path: Path = Path("data/checkpoints.sqlite3")
    policy_path: Path = Path("config/policy.yaml")
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    log_redacted_fields: set[str] = Field(default_factory=set)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)
    coordinator: CoordinatorSettings = Field(default_factory=CoordinatorSettings)
    todoist: TodoistSettings = Field(default_factory=TodoistSettings)
    research: ResearchSettings = Field(default_factory=ResearchSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    local_execution: LocalExecutionSettings = Field(default_factory=LocalExecutionSettings)
    opencode: OpenCodeSettings = Field(default_factory=OpenCodeSettings)
    codex_subscription: CodexSubscriptionSettings = Field(
        default_factory=CodexSubscriptionSettings
    )

    @field_validator("data_dir", "checkpoint_path", "policy_path", mode="after")
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
        if self.deepseek.enabled and self.deepseek.api_key is None:
            raise ValueError("deepseek.api_key is required when deepseek.enabled is true")
        if self.coordinator.enabled:
            if not self.coordinator.models:
                raise ValueError(
                    "coordinator.models requires at least one target when "
                    "coordinator.enabled is true"
                )
            configured_providers = {target.provider.lower() for target in self.coordinator.models}
            if "openai" in configured_providers and not self.openai.enabled:
                raise ValueError("openai must be enabled for an OpenAI coordinator target")
            if "deepseek" in configured_providers and not self.deepseek.enabled:
                raise ValueError("deepseek must be enabled for a DeepSeek coordinator target")
            codex_providers = {"codex", "codex-subscription"}
            if configured_providers & codex_providers and not self.codex_subscription.enabled:
                raise ValueError(
                    "codex_subscription must be enabled for a Codex coordinator target"
                )
        if self.telegram.enabled:
            if self.telegram.bot_token is None:
                raise ValueError("telegram.bot_token is required when telegram.enabled is true")
            if not self.telegram.allowed_chat_ids:
                raise ValueError(
                    "telegram.allowed_chat_ids is required when telegram.enabled is true"
                )
        if self.opencode.enabled:
            if not self.local_execution.enabled:
                raise ValueError("local_execution must be enabled when opencode.enabled is true")
            if not self.deepseek.enabled or self.deepseek.api_key is None:
                raise ValueError("deepseek must be enabled when opencode.enabled is true")
        return self


@lru_cache
def get_settings() -> Settings:
    """Load and cache process-wide application settings."""

    return Settings()
