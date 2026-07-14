"""Structured logging configured to redact credentials before rendering."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, TextIO, cast

import structlog
from pydantic import BaseModel, SecretBytes, SecretStr

from personal_agent.core.config import LogFormat, Settings

REDACTED = "[REDACTED]"

_DEFAULT_SENSITIVE_FIELDS = {
    "api_key",
    "apikey",
    "api_token",
    "authorization",
    "bot_token",
    "client_secret",
    "cookie",
    "password",
    "passwd",
    "private_key",
    "refresh_token",
    "secret",
    "set_cookie",
    "shared_secret",
    "token",
}
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def _normalize_field_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _collect_secret_values(value: object) -> set[str]:
    if isinstance(value, SecretStr):
        secret = value.get_secret_value()
        return {secret} if secret else set()
    if isinstance(value, SecretBytes):
        secret = value.get_secret_value().decode(errors="ignore")
        return {secret} if secret else set()
    if isinstance(value, BaseModel):
        secrets: set[str] = set()
        for field_name in type(value).model_fields:
            secrets.update(_collect_secret_values(getattr(value, field_name)))
        return secrets
    if isinstance(value, Mapping):
        secrets = set()
        for item in value.values():
            secrets.update(_collect_secret_values(item))
        return secrets
    if isinstance(value, (list, tuple, set, frozenset)):
        secrets = set()
        for item in value:
            secrets.update(_collect_secret_values(item))
        return secrets
    return set()


class SecretRedactor:
    """Structlog processor that recursively removes configured credentials."""

    def __init__(self, *, field_names: set[str], secret_values: set[str]) -> None:
        self._field_names = {
            *(_normalize_field_name(name) for name in _DEFAULT_SENSITIVE_FIELDS),
            *(_normalize_field_name(name) for name in field_names),
        }
        self._secret_values = tuple(
            sorted((value for value in secret_values if value), key=len, reverse=True)
        )

    def __call__(
        self,
        logger: object,
        method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> Mapping[str, Any]:
        del logger, method_name
        return {key: self._redact(value, key=key) for key, value in event_dict.items()}

    def redact_text(self, value: str) -> str:
        """Redact configured credentials and authorization tokens from plain text."""

        return cast(str, self._redact(value))

    def _redact(self, value: Any, *, key: object | None = None) -> Any:
        if key is not None and _normalize_field_name(key) in self._field_names:
            return REDACTED
        if isinstance(value, (SecretStr, SecretBytes)):
            return REDACTED
        if isinstance(value, str):
            redacted = value
            for secret in self._secret_values:
                redacted = redacted.replace(secret, REDACTED)
            redacted = _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", redacted)
            return _BEARER_PATTERN.sub(REDACTED, redacted)
        if isinstance(value, Mapping):
            return {
                item_key: self._redact(item_value, key=item_key)
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self._redact(item) for item in value]
        return value


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> None:
    """Configure application and standard-library logs through one safe renderer."""

    redactor = SecretRedactor(
        field_names=settings.log_redacted_fields,
        secret_values=_collect_secret_values(settings),
    )
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: Any
    if settings.log_format is LogFormat.JSON:
        renderer = structlog.processors.JSONRenderer(sort_keys=True)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redactor,
            renderer,
        ],
    )
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level)
    logging.captureWarnings(True)


def redact_sensitive_text(settings: Settings, value: str) -> str:
    """Apply the logging redaction policy before persisting user-visible text."""

    return SecretRedactor(
        field_names=settings.log_redacted_fields,
        secret_values=_collect_secret_values(settings),
    ).redact_text(value)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a configured structured logger."""

    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
