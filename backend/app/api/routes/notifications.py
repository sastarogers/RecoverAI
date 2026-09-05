"""Outbound customer messages (§46 audit, §59 traceability)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session
from app.api.envelope import ok, paginated
from app.core.config import settings
from app.db.models import Customer, NotificationMessage, RecoveryOpportunity
from app.domain.enums import MessageStatus

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/status")
async def messaging_status(session: AsyncSession = Depends(db_session)) -> dict:
    """Channel configuration and delivery counters. Never returns credentials."""
    rows = (
        await session.execute(
            select(NotificationMessage.status, func.count(NotificationMessage.id)).group_by(
                NotificationMessage.status
            )
        )
    ).all()
    by_status = {str(s): int(n) for s, n in rows}

    delivered = (
        await session.execute(
            select(func.count(NotificationMessage.id)).where(
                NotificationMessage.delivered_externally.is_(True)
            )
        )
    ).scalar_one()

    return ok(
        {
            "enabled": settings.messaging_enabled,
            "live": settings.messaging_live,
            "provider": "twilio" if settings.twilio_configured else None,
            "channels": {
                "whatsapp": settings.whatsapp_configured,
                "sms": settings.sms_configured,
            },
            "preferred_channel": settings.messaging_preferred_channel,
            "messages_by_status": by_status,
            "delivered_externally": int(delivered),
            "note": (
                "Messages are composed and recorded even when no provider is configured. "
                "A delivered message is a recovery action, never recovered revenue."
            ),
        }
    )


@router.get("")
async def list_messages(
    opportunity_id: uuid.UUID | None = None,
    status: MessageStatus | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(db_session),
) -> dict:
    stmt = (
        select(NotificationMessage, RecoveryOpportunity, Customer)
        .join(
            RecoveryOpportunity,
            RecoveryOpportunity.id == NotificationMessage.opportunity_id,
        )
        .join(Customer, Customer.id == NotificationMessage.customer_id)
    )
    count_stmt = select(func.count(NotificationMessage.id))
    if opportunity_id:
        stmt = stmt.where(NotificationMessage.opportunity_id == opportunity_id)
        count_stmt = count_stmt.where(NotificationMessage.opportunity_id == opportunity_id)
    if status:
        stmt = stmt.where(NotificationMessage.status == status)
        count_stmt = count_stmt.where(NotificationMessage.status == status)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(desc(NotificationMessage.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return paginated(
        [serialize_message(m, opportunity=o, customer=c) for m, o, c in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def serialize_message(message, *, opportunity=None, customer=None) -> dict:
    return {
        "id": str(message.id),
        "channel": message.channel,
        "provider": message.provider,
        "template": message.template,
        "recipient": message.recipient_masked,
        "body": message.body,
        "action_url": message.action_url,
        "status": message.status,
        "delivered_externally": message.delivered_externally,
        "provider_message_id": message.provider_message_id,
        "error": message.error,
        "reason": message.reason,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "opportunity_ref": opportunity.opportunity_ref if opportunity else None,
        "customer_ref": customer.customer_ref if customer else None,
    }
