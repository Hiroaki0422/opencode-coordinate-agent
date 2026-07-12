"""Versioned contract for the future Codex subscription CLI provider."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, order=True)
class CodexCliVersion:
    major: int
    minor: int
    patch: int
    release_rank: int
    prerelease_number: int


MINIMUM_CODEX_CLI_VERSION = CodexCliVersion(0, 144, 0, 0, 4)

REQUIRED_EXEC_FLAGS = (
    "--sandbox",
    "--skip-git-repo-check",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--output-schema",
    "--json",
    "--output-last-message",
)


class CodexCliFailure(StrEnum):
    """Sanitized failure classes used by the future process adapter."""

    MISSING_EXECUTABLE = "missing_executable"
    MISSING_LOGIN = "missing_login"
    EXPIRED_AUTHORIZATION = "expired_authorization"
    SUBSCRIPTION_EXHAUSTED = "subscription_exhausted"
    RATE_LIMITED = "rate_limited"
    MALFORMED_OUTPUT = "malformed_output"
    UNSUPPORTED_VERSION = "unsupported_version"
    TIMEOUT = "timeout"
    PROCESS_FAILURE = "process_failure"
    UNSUPPORTED_MODEL = "unsupported_model"


class CodexCliContractError(ValueError):
    """Raised when installed CLI behavior does not match the pinned contract."""


def parse_codex_cli_version(output: str) -> CodexCliVersion:
    """Parse `codex --version` output without accepting unknown prerelease formats."""

    match = re.fullmatch(
        r"codex-cli (\d+)\.(\d+)\.(\d+)(?:-alpha\.(\d+))?",
        output.strip(),
    )
    if match is None:
        raise CodexCliContractError("Codex CLI returned an unrecognized version")
    major, minor, patch, alpha = match.groups()
    return CodexCliVersion(
        major=int(major),
        minor=int(minor),
        patch=int(patch),
        release_rank=0 if alpha is not None else 1,
        prerelease_number=int(alpha or 0),
    )


def require_supported_version(output: str) -> CodexCliVersion:
    """Reject CLI versions older than the locally verified contract floor."""

    version = parse_codex_cli_version(output)
    if version < MINIMUM_CODEX_CLI_VERSION:
        raise CodexCliContractError("Codex CLI version is unsupported")
    return version


def validate_exec_help(output: str) -> None:
    """Ensure non-interactive safety and structured-output flags are present."""

    missing = [flag for flag in REQUIRED_EXEC_FLAGS if flag not in output]
    if missing:
        raise CodexCliContractError(
            f"Codex CLI is missing required exec flags: {', '.join(missing)}"
        )
    if "read-only" not in output:
        raise CodexCliContractError("Codex CLI does not advertise read-only sandbox mode")


def parse_jsonl_events(output: str) -> list[dict[str, Any]]:
    """Validate JSONL framing without depending on unstable event-specific fields."""

    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CodexCliContractError("Codex CLI returned malformed JSONL") from error
        if not isinstance(event, dict):
            raise CodexCliContractError("Codex CLI JSONL events must be objects")
        events.append(event)
    if not events:
        raise CodexCliContractError("Codex CLI returned no JSONL events")
    return events


def classify_codex_cli_failure(
    *,
    exit_code: int | None = None,
    stderr: str = "",
    executable_missing: bool = False,
    timed_out: bool = False,
    malformed_output: bool = False,
    unsupported_version: bool = False,
) -> CodexCliFailure | None:
    """Map process failures to stable categories without preserving sensitive details."""

    if executable_missing:
        return CodexCliFailure.MISSING_EXECUTABLE
    if timed_out:
        return CodexCliFailure.TIMEOUT
    if unsupported_version:
        return CodexCliFailure.UNSUPPORTED_VERSION
    if malformed_output:
        return CodexCliFailure.MALFORMED_OUTPUT
    if exit_code == 0:
        return None

    normalized = stderr.casefold()
    if "not logged in" in normalized or "login required" in normalized:
        return CodexCliFailure.MISSING_LOGIN
    if any(
        marker in normalized
        for marker in (
            "invalid_grant",
            "refresh token",
            "refresh_token_reused",
            "token expired",
            "token_expired",
            "token revoked",
            "token_revoked",
            "authorization expired",
        )
    ):
        return CodexCliFailure.EXPIRED_AUTHORIZATION
    if any(
        marker in normalized
        for marker in ("usage limit", "subscription limit", "quota exhausted")
    ):
        return CodexCliFailure.SUBSCRIPTION_EXHAUSTED
    if "rate limit" in normalized or "too many requests" in normalized or "http 429" in normalized:
        return CodexCliFailure.RATE_LIMITED
    if "model" in normalized and any(
        marker in normalized for marker in ("not supported", "not found", "unknown model")
    ):
        return CodexCliFailure.UNSUPPORTED_MODEL
    if any(
        marker in normalized
        for marker in ("invalid schema", "json schema", "response_format")
    ):
        return CodexCliFailure.MALFORMED_OUTPUT
    return CodexCliFailure.PROCESS_FAILURE
