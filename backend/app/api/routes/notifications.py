"""Outbound customer messages (§46 audit, §59 traceability)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session
from app.api.envelope import ok, paginated
from app.core.config import settings
from app.db.models import Customer, NotificationMessage, RecoveryOpportunity
from app.domain.enums import MessageChannel, MessageStatus

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


def _explain_result(result) -> str:
    """Say plainly what happened, including when the answer is 'nothing was sent'."""
    if result.delivered_externally:
        return "Delivered to the handset via Twilio."
    if result.status is MessageStatus.FAILED:
        return result.details.get("guidance") or (
            result.error or "The provider rejected the message."
        )
    return (
        "Composed but not delivered — set MESSAGING_ENABLED and Twilio credentials "
        "to send for real."
    )


class TestMessageRequest(BaseModel):
    """A real send, to a number you control, so delivery can be proven on stage."""

    to: str = Field(min_length=6, max_length=20, description="E.164, e.g. +919812345678")
    channel: MessageChannel = MessageChannel.WHATSAPP
    customer_name: str | None = "Rahul"
    amount_minor: int = Field(default=99_900, ge=100, le=100_000_000)


@router.post("/test")
async def send_test_message(body: TestMessageRequest) -> dict:
    """Compose the real expired-card message and attempt to deliver it.

    This is the demonstration path: it renders exactly the template a live recovery
    would send, and — when Twilio is configured — actually delivers it. With no
    provider configured it returns the composed text and says plainly that nothing
    was sent, rather than implying a delivery that did not happen.
    """
    from app.domain.enums import FailureCategory, Scenario
    from app.notifications.base import OutboundMessage, mask_recipient
    from app.notifications.simulated import SimulatedChannel
    from app.notifications.templates import render_payment_method_update

    rendered = render_payment_method_update(
        channel=body.channel,
        merchant="RecoverAI Merchant",
        customer_name=body.customer_name,
        scenario=Scenario.FAILED_SUBSCRIPTION,
        category=FailureCategory.EXPIRED_CARD,
        amount_minor=body.amount_minor,
        action_url=None,
        plan_name="Pro Monthly",
    )

    channel_impl = SimulatedChannel()
    if settings.messaging_live:
        from app.notifications.twilio import TwilioChannel

        try:
            channel_impl = TwilioChannel()
        except RuntimeError:
            channel_impl = SimulatedChannel()

    try:
        result = await channel_impl.send(
            OutboundMessage(
                channel=body.channel,
                to=body.to,
                body=rendered.body,
                template=rendered.template,
            )
        )
    finally:
        await channel_impl.aclose()

    return ok(
        {
            "channel": str(body.channel),
            "to": mask_recipient(body.to),
            "body": rendered.body,
            "status": str(result.status),
            "provider": result.provider,
            "delivered_externally": result.delivered_externally,
            "provider_message_id": result.provider_message_id,
            "error": result.error,
            "explanation": _explain_result(result),
            "guidance": result.details.get("guidance"),
            "body_substituted": result.body_substituted,
            "delivered_body": result.details.get("delivered_body"),
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
