"""Offline regression tests for the verified Codex CLI contract."""

import pytest

from personal_agent.models import (
    MINIMUM_CODEX_CLI_VERSION,
    CodexCliContractError,
    CodexCliFailure,
    classify_codex_cli_failure,
    parse_jsonl_events,
    require_supported_version,
    validate_exec_help,
)


def test_verified_alpha_version_is_supported() -> None:
    assert require_supported_version("codex-cli 0.144.0-alpha.4") == (
        MINIMUM_CODEX_CLI_VERSION
    )
    assert require_supported_version("codex-cli 0.144.0") > MINIMUM_CODEX_CLI_VERSION


@pytest.mark.parametrize(
    "output",
    ["codex-cli 0.143.9", "codex-cli 0.144.0-alpha.3", "codex unknown"],
)
def test_unsupported_or_unknown_versions_are_rejected(output: str) -> None:
    with pytest.raises(CodexCliContractError):
        require_supported_version(output)


def test_exec_help_requires_safety_and_structured_output_flags() -> None:
    help_output = " ".join(
        [
            "--sandbox read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--output-schema",
            "--json",
            "--output-last-message",
        ]
    )

    validate_exec_help(help_output)

    with pytest.raises(CodexCliContractError, match="output-schema"):
        validate_exec_help(help_output.replace("--output-schema", ""))


def test_jsonl_parser_rejects_non_object_or_malformed_events() -> None:
    assert parse_jsonl_events('{"type":"started"}\n{"type":"completed"}') == [
        {"type": "started"},
        {"type": "completed"},
    ]
    with pytest.raises(CodexCliContractError, match="objects"):
        parse_jsonl_events('["event"]')
    with pytest.raises(CodexCliContractError, match="malformed"):
        parse_jsonl_events("not-json")


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"executable_missing": True}, CodexCliFailure.MISSING_EXECUTABLE),
        ({"timed_out": True}, CodexCliFailure.TIMEOUT),
        ({"unsupported_version": True}, CodexCliFailure.UNSUPPORTED_VERSION),
        ({"malformed_output": True}, CodexCliFailure.MALFORMED_OUTPUT),
        ({"exit_code": 1, "stderr": "Not logged in"}, CodexCliFailure.MISSING_LOGIN),
        (
            {"exit_code": 1, "stderr": "refresh token invalid_grant"},
            CodexCliFailure.EXPIRED_AUTHORIZATION,
        ),
        (
            {"exit_code": 1, "stderr": "subscription usage limit reached"},
            CodexCliFailure.SUBSCRIPTION_EXHAUSTED,
        ),
        ({"exit_code": 1, "stderr": "HTTP 429 rate limit"}, CodexCliFailure.RATE_LIMITED),
        ({"exit_code": 2, "stderr": "unexpected argument"}, CodexCliFailure.PROCESS_FAILURE),
        ({"exit_code": 0}, None),
    ],
)
def test_failure_classification_is_stable(
    arguments: dict[str, object],
    expected: CodexCliFailure | None,
) -> None:
    assert classify_codex_cli_failure(**arguments) is expected  # type: ignore[arg-type]
