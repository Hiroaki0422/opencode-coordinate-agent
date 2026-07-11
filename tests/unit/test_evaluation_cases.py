"""Tests for the versioned P0/P1 evaluation catalog."""

from pathlib import Path

from personal_agent.evaluations import EvaluationCategory, load_cases


def test_p0_p1_catalog_covers_required_regressions() -> None:
    path = Path("tests/fixtures/evaluations/p0_p1.json")
    cases = load_cases(path)
    categories = {case.category for case in cases}

    assert categories == set(EvaluationCategory)
    prompt_injection = next(
        case for case in cases if case.category is EvaluationCategory.PROMPT_INJECTION
    )
    assert prompt_injection.source_content is not None
    assert "reveal API keys" in prompt_injection.source_content
