"""Tests for structured logging and recursive secret redaction."""

import json
import logging
from io import StringIO

from pydantic import SecretStr

from personal_agent.core.config import LogFormat, OpenAISettings, Settings
from personal_agent.observability.logging import (
    REDACTED,
    SecretRedactor,
    configure_logging,
    redact_sensitive_text,
)


def test_redactor_handles_nested_fields_exact_secrets_and_headers() -> None:
    redactor = SecretRedactor(
        field_names={"account_number"},
        secret_values={"exact-secret"},
    )

    event = redactor(
        None,
        "info",
        {
            "event": "token exact-secret Authorization: Bearer header-secret",
            "headers": {"Authorization": "Bearer nested-secret"},
            "account_number": "1234",
            "items": [{"api_key": "key-value"}],
        },
    )

    assert "exact-secret" not in event["event"]
    assert "header-secret" not in event["event"]
    assert event["headers"]["Authorization"] == REDACTED
    assert event["account_number"] == REDACTED
    assert event["items"][0]["api_key"] == REDACTED


def test_json_logging_redacts_settings_secrets_and_standard_logs() -> None:
    stream = StringIO()
    settings = Settings(
        log_format=LogFormat.JSON,
        log_redacted_fields={"customer_id"},
        openai=OpenAISettings(enabled=True, api_key=SecretStr("openai-secret")),
    )
    configure_logging(settings, stream=stream)

    logging.getLogger("foreign").warning(
        "Authorization: Bearer foreign-secret customer=%s",
        "openai-secret",
        extra={"customer_id": "customer-123"},
    )

    rendered = stream.getvalue().strip()
    event = json.loads(rendered)

    assert "openai-secret" not in rendered
    assert "foreign-secret" not in rendered
    assert event["event"].count(REDACTED) == 2


def test_sensitive_text_redaction_reuses_configured_logging_policy() -> None:
    settings = Settings(
        openai=OpenAISettings(enabled=True, api_key=SecretStr("persist-secret"))
    )

    redacted = redact_sensitive_text(
        settings,
        "persist-secret Authorization: Bearer header-secret",
    )

    assert "persist-secret" not in redacted
    assert "header-secret" not in redacted
    assert redacted.count(REDACTED) == 2
