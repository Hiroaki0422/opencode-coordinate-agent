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
from personal_agent.models.coordinator import PydanticCoordinator

ModelBuilder = Callable[[ModelTarget, Settings], Model]


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
) -> PydanticCoordinator:
    """Build the configured coordinator and its ordered fallback route."""

    if not settings.coordinator.enabled:
        raise ValueError("the coordinator is disabled")
    selected_registry = registry or default_model_registry()
    model = selected_registry.build_route(settings.coordinator.models, settings)
    return PydanticCoordinator(model)
