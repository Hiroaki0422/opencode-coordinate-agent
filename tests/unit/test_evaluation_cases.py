"""Tests for versioned safety and coding evaluation catalogs."""

from pathlib import Path

from personal_agent.evaluations import EvaluationCategory, load_cases


def test_p0_p1_catalog_covers_required_regressions() -> None:
    path = Path("tests/fixtures/evaluations/p0_p1.json")
    cases = load_cases(path)
    categories = {case.category for case in cases}

    assert categories == {
        EvaluationCategory.ORDINARY_ACTION,
        EvaluationCategory.EXPIRED_GRANT,
        EvaluationCategory.WRONG_RESOURCE_SCOPE,
        EvaluationCategory.DUPLICATE_RESUME,
        EvaluationCategory.PROMPT_INJECTION,
        EvaluationCategory.PROVIDER_FAILURE,
    }
    prompt_injection = next(
        case for case in cases if case.category is EvaluationCategory.PROMPT_INJECTION
    )
    assert prompt_injection.source_content is not None
    assert "reveal API keys" in prompt_injection.source_content


def test_p3_catalog_covers_coding_regressions() -> None:
    cases = load_cases(Path("tests/fixtures/evaluations/p3_coding.json"))

    assert {case.category for case in cases} == {
        EvaluationCategory.REPOSITORY_CREATION,
        EvaluationCategory.WORKSPACE_ESCAPE,
        EvaluationCategory.FAILED_TESTS,
        EvaluationCategory.CHANGE_VERIFICATION,
    }
