"""Structured logging. Secrets are never logged (see redact())."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import settings

_SECRET_HINTS = ("secret", "key", "token", "password", "authorization", "signature")


def redact(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if any(h in k.lower() for h in _SECRET_HINTS):
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
