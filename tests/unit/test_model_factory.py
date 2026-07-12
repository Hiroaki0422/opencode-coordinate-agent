"""Tests for provider-neutral ordered fallback construction."""

from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.test import TestModel

from personal_agent.core.config import ModelTarget, Settings
from personal_agent.models import (
    CodexCliRunner,
    CodexSubscriptionCoordinator,
    FallbackCoordinator,
    ModelRegistry,
    build_coordinator,
)


def test_registry_builds_ordered_fallback_route() -> None:
    registry = ModelRegistry()
    registry.register("primary", lambda target, settings: TestModel())
    registry.register("secondary", lambda target, settings: TestModel())
    targets = [
        ModelTarget(provider="primary", model="model-a"),
        ModelTarget(provider="secondary", model="model-b"),
    ]

    route = registry.build_route(targets, Settings())

    assert isinstance(route, FallbackModel)
    assert len(route.models) == 2


def test_registry_rejects_unknown_provider() -> None:
    registry = ModelRegistry()

    try:
        registry.build(ModelTarget(provider="unknown", model="model"), Settings())
    except ValueError as error:
        assert "not registered" in str(error)
    else:
        raise AssertionError("unknown providers must be rejected")


def test_factory_builds_codex_only_without_api_key() -> None:
    settings = Settings(
        codex_subscription={"enabled": True},
        coordinator={
            "enabled": True,
            "models": [{"provider": "codex-subscription", "model": "gpt-5.4"}],
        },
    )

    coordinator = build_coordinator(
        settings,
        codex_runner_builder=lambda active: CodexCliRunner(active.codex_subscription),
    )

    assert isinstance(coordinator, CodexSubscriptionCoordinator)


def test_factory_preserves_mixed_provider_order() -> None:
    registry = ModelRegistry()
    registry.register("api", lambda target, settings: TestModel())
    settings = Settings(
        codex_subscription={"enabled": True},
        coordinator={
            "enabled": True,
            "models": [
                {"provider": "api", "model": "first"},
                {"provider": "codex-subscription", "model": "second"},
                {"provider": "api", "model": "third"},
            ],
        },
    )

    coordinator = build_coordinator(
        settings,
        registry=registry,
        codex_runner_builder=lambda active: CodexCliRunner(active.codex_subscription),
    )

    assert isinstance(coordinator, FallbackCoordinator)
