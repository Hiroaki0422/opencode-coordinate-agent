"""Load versioned deterministic safety and tool evaluation cases."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class EvaluationCategory(StrEnum):
    ORDINARY_ACTION = "ordinary_action"
    EXPIRED_GRANT = "expired_grant"
    WRONG_RESOURCE_SCOPE = "wrong_resource_scope"
    DUPLICATE_RESUME = "duplicate_resume"
    PROMPT_INJECTION = "prompt_injection"
    PROVIDER_FAILURE = "provider_failure"


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: EvaluationCategory
    prompt: str
    expected_outcome: str
    source_content: str | None = None
    notes: str | None = None


def load_cases(path: Path) -> list[EvaluationCase]:
    """Load and validate a JSON evaluation catalog with unique identifiers."""

    payload = json.loads(path.read_text())
    cases = [EvaluationCase.model_validate(item) for item in payload]
    identifiers = [case.id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("evaluation case identifiers must be unique")
    return cases
