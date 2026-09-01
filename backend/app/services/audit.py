"""Audit trail helper (§59). Never records secrets."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import utcnow
from app.core.logging import redact
from app.db.models import AuditLog
from app.domain.enums import Actor


async def record(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: str | None,
    actor: Actor,
    action: str,
    detail: dict[str, Any] | None = None,
    simulation_run_id: uuid.UUID | None = None,
) -> AuditLog:
    entry = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        action=action,
        detail=redact(detail or {}),
        simulation_run_id=simulation_run_id,
        occurred_at=utcnow(),
    )
    session.add(entry)
    return entry
