"""Razorpay Test Mode endpoints (§44/§51)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import serializers as ser
from app.api.deps import db_session
from app.api.envelope import ok
from app.core.config import settings
from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.db.models import RecoveryOpportunity, WebhookEvent
from app.domain.enums import Source, WebhookStatus
from app.integrations.razorpay import client as rzp
from app.integrations.razorpay.signature import verify_webhook_signature
from app.integrations.razorpay.webhooks import record_and_process

log = get_logger("recoverai.api.razorpay")

router = APIRouter(tags=["razorpay"])


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(None, alias="X-Razorpay-Signature"),
    x_razorpay_event_id: str | None = Header(None, alias="X-Razorpay-Event-Id"),
    session: AsyncSession = Depends(db_session),
) -> JSONResponse:
    """Signature-verified, idempotent webhook ingestion.

    The signature is computed over the raw body, so the body is read as bytes before
    any JSON parsing.
    """
    raw_body = await request.body()
    signature_valid = verify_webhook_signature(raw_body, x_razorpay_signature)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_PAYLOAD", "message": "Body is not valid JSON"}},
        )

    result = await record_and_process(
        session,
        payload=payload,
        raw_event_id=x_razorpay_event_id,
        signature_valid=signature_valid,
    )
    await session.commit()

    # Duplicates return 200: Razorpay must not be told to retry an event we already have.
    status_code = 403 if result.status is WebhookStatus.INVALID else 200
    return JSONResponse(
        status_code=status_code,
        content={
            "data": {
                "status": str(result.status),
                "message": result.message,
                "event_type": result.event_type,
                "opportunity_ref": result.opportunity_ref,
                "settled_amount_minor": result.settled_amount_minor,
            }
        },
    )


@router.get("/razorpay/status")
async def razorpay_status(session: AsyncSession = Depends(db_session)) -> dict:
    """Connection status for the Razorpay panel (§44). Never returns secrets."""
    status = await rzp.status()

    recent = (
        (
            await session.execute(
                select(WebhookEvent).order_by(desc(WebhookEvent.received_at)).limit(15)
            )
        )
        .scalars()
        .all()
    )
    live_opportunities = (
        (
            await session.execute(
                select(RecoveryOpportunity)
                .where(RecoveryOpportunity.source == Source.RAZORPAY)
                .order_by(desc(RecoveryOpportunity.detected_at))
                .limit(10)
            )
        )
        .scalars()
        .all()
    )

    return ok(
        {
            "integration": {
                "configured": status.configured,
                "webhook_configured": status.webhook_configured,
                "enabled": status.enabled,
                "reachable": status.reachable,
                "key_id": status.key_id_masked,
                "mode": "TEST",
                "error": status.error,
            },
            "webhook": {
                "endpoint": "/api/webhooks/razorpay",
                "events_received": len(recent),
                "last_event_at": recent[0].received_at.isoformat() if recent else None,
                "signature_verification": "enabled" if status.webhook_configured else "not configured",
            },
            "recent_events": [
                {
                    "id": str(e.id),
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "signature_valid": e.signature_valid,
                    "status": e.processing_status,
                    "received_at": e.received_at.isoformat(),
                    "error": e.error,
                }
                for e in recent
            ],
            "live_opportunities": [ser.opportunity_row(o) for o in live_opportunities],
        }
    )


class TestPaymentRequest(BaseModel):
    amount_minor: int = Field(500_000, ge=100, le=100_000_000)
    description: str = Field("RecoverAI Test Mode payment", max_length=200)
    customer_ref: str | None = None


@router.post("/razorpay/test-payment")
async def create_test_payment(body: TestPaymentRequest) -> dict:
    """Create a Test Mode payment link so a judge can pay and watch the webhook land."""
    if not settings.razorpay_configured:
        raise IntegrationError(
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
        )
    client = rzp.RazorpayClient()
    link = await client.create_payment_link(
        amount_minor=body.amount_minor,
        description=body.description,
        notes={"recoverai_demo": "true", "recoverai_customer_ref": body.customer_ref or ""},
    )
    return ok(
        {
            "payment_link_id": link.get("id"),
            "short_url": link.get("short_url"),
            "amount": ser.money(body.amount_minor),
            "status": link.get("status"),
            "mode": "TEST",
        }
    )


@router.get("/razorpay/events")
async def list_webhook_events(
    limit: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(db_session)
) -> dict:
    events = (
        (
            await session.execute(
                select(WebhookEvent).order_by(desc(WebhookEvent.received_at)).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return ok(
        [
            {
                "id": str(e.id),
                "provider": e.provider,
                "event_id": e.event_id,
                "event_type": e.event_type,
                "signature_valid": e.signature_valid,
                "status": e.processing_status,
                "received_at": e.received_at.isoformat(),
                "processed_at": e.processed_at.isoformat() if e.processed_at else None,
                "error": e.error,
            }
            for e in events
        ]
    )
