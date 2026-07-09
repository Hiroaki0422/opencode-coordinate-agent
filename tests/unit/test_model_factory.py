"""Tests for provider-neutral ordered fallback construction."""

from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.test import TestModel

from personal_agent.core.config import ModelTarget, Settings
from personal_agent.models import ModelRegistry


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
