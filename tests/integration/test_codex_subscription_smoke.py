"""Explicit opt-in authenticated Codex subscription smoke test."""

import os
from pathlib import Path

import pytest

from personal_agent.core.config import CodexSubscriptionSettings
from personal_agent.models import CodexCliRunner, CodexSubscriptionCoordinator


@pytest.mark.skipif(
    os.getenv("PERSONAL_AGENT_RUN_CODEX_SMOKE") != "true",
    reason="set PERSONAL_AGENT_RUN_CODEX_SMOKE=true to consume Codex subscription usage",
)
async def test_authenticated_codex_subscription_decision(tmp_path: Path) -> None:
    settings = CodexSubscriptionSettings(
        enabled=True,
        working_directory=tmp_path,
    )
    coordinator = CodexSubscriptionCoordinator(
        runner=CodexCliRunner(settings),
        settings=settings,
        model=settings.model,
    )

    await coordinator.health_check()
    decision = await coordinator.decide(
        "Reply with a brief greeting. Do not propose an external action."
    )

    assert decision.message
    assert decision.action is None
