"""Structured logging. Secrets are never logged (see redact())."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import settings

_SECRET_HINTS = ("secret", "key", "token", "password", "authorization", "signature")

#: Fields that merely *end in* a secret-sounding word but are identifiers, not
#: credentials. These are the values the audit trail exists to expose — a settlement key
#: is the evidence that revenue was counted exactly once, so masking it defeats the
#: purpose of recording it (§59).
_NON_SECRET_FIELDS = frozenset(
    {
        "settlement_key",
        "dedupe_key",
        "idempotency_key",
        "simulation_key",
        "context_signature",
        "reproducibility_key",
        "attribution_token",
    }
)


def redact(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        lowered = k.lower()
        if lowered in _NON_SECRET_FIELDS:
            out[k] = redact(v) if isinstance(v, dict) else v
        elif any(h in lowered for h in _SECRET_HINTS):
            out[k] = "***redacted***"
        elif isinstance(v, dict):
            out[k] = redact(v)
        else:
            out[k] = v
    return out


def _redact_processor(_logger, _name, event_dict):
    return redact(event_dict)


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=getattr(logging, settings.log_level, 20)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.dev.ConsoleRenderer(colors=settings.environment == "development"),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level, 20)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
