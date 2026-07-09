"""Typed coordinator contract and PydanticAI implementation."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model

from personal_agent.core.types import ActionRequest

COORDINATOR_INSTRUCTIONS = """
You are the coordinator for a personal agent. Respond directly when no external action is needed.
When a tool action is needed, return exactly one typed ActionRequest. Classify reads as read,
ordinary changes as write, and destructive, installation, credential, external-message, or
remote-push actions as risky. You only propose actions; deterministic policy code decides whether
they may execute.
""".strip()


class CoordinatorDecision(BaseModel):
    """A user-facing response plus an optional policy-checked action proposal."""

    message: str
    action: ActionRequest | None = None


class Coordinator(Protocol):
    async def decide(self, user_input: str) -> CoordinatorDecision:
        """Interpret one user request."""


class PydanticCoordinator:
    """Coordinator backed by a provider-neutral PydanticAI model route."""

    def __init__(self, model: Model) -> None:
        self._agent = Agent(
            model,
            output_type=CoordinatorDecision,
            instructions=COORDINATOR_INSTRUCTIONS,
            name="personal-agent-coordinator",
        )

    async def decide(self, user_input: str) -> CoordinatorDecision:
        result = await self._agent.run(user_input)
        return result.output
