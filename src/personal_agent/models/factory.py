"""Extensible provider registry and ordered model fallback construction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

from personal_agent.core.config import ModelTarget, Settings
from personal_agent.models.codex_cli import CodexCliRunner
from personal_agent.models.codex_subscription import CodexSubscriptionCoordinator
from personal_agent.models.coordinator import Coordinator, FallbackCoordinator, PydanticCoordinator

ModelBuilder = Callable[[ModelTarget, Settings], Model]
CodexRunnerBuilder = Callable[[Settings], CodexCliRunner]


class ModelRegistry:
    """Map provider names to model builders without leaking providers into graph code."""

    def __init__(self) -> None:
        self._builders: dict[str, ModelBuilder] = {}

    def register(self, provider: str, builder: ModelBuilder) -> None:
        self._builders[provider.lower()] = builder

    def build(self, target: ModelTarget, settings: Settings) -> Model:
        builder = self._builders.get(target.provider.lower())
        if builder is None:
            raise ValueError(f"model provider {target.provider!r} is not registered")
        return builder(target, settings)

    def build_route(self, targets: list[ModelTarget], settings: Settings) -> Model:
        if not targets:
            raise ValueError("a model route requires at least one target")
        models = [self.build(target, settings) for target in targets]
        if len(models) == 1:
            return models[0]
        return FallbackModel(models[0], *models[1:])


def _build_openai(target: ModelTarget, settings: Settings) -> Model:
    if settings.openai.api_key is None:
        raise ValueError("OpenAI API key is not configured")
    provider = OpenAIProvider(api_key=settings.openai.api_key.get_secret_value())
    return OpenAIResponsesModel(cast(Any, target.model), provider=provider)


def _build_deepseek(target: ModelTarget, settings: Settings) -> Model:
    if settings.deepseek.api_key is None:
        raise ValueError("DeepSeek API key is not configured")
    provider = DeepSeekProvider(api_key=settings.deepseek.api_key.get_secret_value())
    return OpenAIChatModel(cast(Any, target.model), provider=provider)


def default_model_registry() -> ModelRegistry:
    registry = ModelRegistry()
    registry.register("openai", _build_openai)
    registry.register("deepseek", _build_deepseek)
    return registry


def build_coordinator(
    settings: Settings,
    *,
    registry: ModelRegistry | None = None,
    codex_runner_builder: CodexRunnerBuilder | None = None,
) -> Coordinator:
    """Build the configured coordinator and its ordered fallback route."""

    if not settings.coordinator.enabled:
        raise ValueError("the coordinator is disabled")
    selected_registry = registry or default_model_registry()
    selected_codex_builder = codex_runner_builder or (
        lambda active_settings: CodexCliRunner(active_settings.codex_subscription)
    )
    candidates: list[tuple[str, Coordinator]] = []
    api_targets: list[ModelTarget] = []

    def flush_api_targets() -> None:
        if not api_targets:
            return
        label = "+".join(target.provider for target in api_targets)
        model = selected_registry.build_route(list(api_targets), settings)
        candidates.append((label, PydanticCoordinator(model)))
        api_targets.clear()

    for target in settings.coordinator.models:
        if target.provider.lower() not in {"codex", "codex-subscription"}:
            api_targets.append(target)
            continue
        flush_api_targets()
        candidates.append(
            (
                "codex-subscription",
                CodexSubscriptionCoordinator(
                    runner=selected_codex_builder(settings),
                    settings=settings.codex_subscription,
                    model=target.model or settings.codex_subscription.model,
                ),
            )
        )
    flush_api_targets()
    if len(candidates) == 1:
        return candidates[0][1]
    return FallbackCoordinator(candidates)
