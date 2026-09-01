"""Identifier helpers.

Two kinds of id exist side by side:
  * UUID primary keys — internal, stable, used for all foreign keys.
  * Human refs (OPP0001, C0001, RA0001) — demo-readable, unique, shown in the UI.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def short_id(prefix: str, n: int = 12) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:n]}"


def ref(prefix: str, number: int, width: int = 4) -> str:
    """ref('OPP', 1) -> 'OPP0001'"""
    return f"{prefix}{number:0{width}d}"


def utcnow() -> datetime:
    return datetime.now(UTC)
