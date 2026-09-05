"""Notification service.

Decides *whether* to contact a customer, on which channel, and records the result.

Three guards matter here, in order of how badly they would hurt if missing:

1. **A simulation never reaches the real network.** An opportunity whose source is
   SIMULATOR is pinned to the simulated channel regardless of configuration. Without
   this, a 1,000-customer run would send a thousand real WhatsApp messages.
2. **A message is not a recovery.** Nothing in this module writes to the ledger, sets an
   outcome, or touches `recovered_amount_minor`. Sending is an action; paying is an
   outcome, and only a payment produces the latter.
3. **Opt-out and contactability are honoured before intent.** A customer who asked not
   to be messaged is not messaged, however good the recovery case looks.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import utcnow
from app.core.logging import get_logger
from app.db.models import Customer, NotificationMessage, RecoveryAttempt, RecoveryOpportunity
from app.domain.enums import (
    Actor,
    FailureCategory,
    MessageChannel,
    MessageStatus,
    Scenario,
    Source,
    requires_payment_method_update,
)
from app.notifications.base import NotificationChannel, OutboundMessage, mask_recipient
from app.notifications.simulated import SimulatedChannel
from app.notifications.templates import render_payment_method_update
from app.services import audit

log = get_logger("recoverai.notifications")

MERCHANT_NAME = "RecoverAI Merchant"


@dataclass(slots=True)
class DispatchOutcome:
    sent: bool
    status: MessageStatus
    channel: MessageChannel | None
    reason: str
    message_id: str | None = None


def _select_channel(customer: Customer) -> MessageChannel | None:
    """Preferred channel when it is usable, otherwise the other one."""
    if not customer.phone:
        return None
    preferred = (
        MessageChannel.WHATSAPP
        if settings.messaging_preferred_channel.lower() == "whatsapp"
        else MessageChannel.SMS
    )
    other = (
        MessageChannel.SMS if preferred is MessageChannel.WHATSAPP else MessageChannel.WHATSAPP
    )
    if not settings.messaging_live:
        # Nothing is going out anyway; record the intent on the preferred channel.
        return preferred
    usable = {
        MessageChannel.WHATSAPP: settings.whatsapp_configured,
        MessageChannel.SMS: settings.sms_configured,
    }
    if usable[preferred]:
        return preferred
    return other if usable[other] else None


def _build_channel(opportunity: RecoveryOpportunity) -> NotificationChannel:
    """Simulated unless this is a live opportunity *and* messaging is truly configured."""
    if str(opportunity.source) != str(Source.RAZORPAY):
        return SimulatedChannel()
    if not settings.messaging_live:
        return SimulatedChannel()
    from app.notifications.twilio import TwilioChannel

    try:
        return TwilioChannel()
    except RuntimeError:
        return SimulatedChannel()


async def notify_payment_method_expired(
    session: AsyncSession,
    *,
    opportunity: RecoveryOpportunity,
    customer: Customer,
    attempt: RecoveryAttempt | None = None,
    action_url: str | None = None,
    plan_name: str | None = None,
) -> DispatchOutcome:
    """Tell a customer their stored payment method needs replacing.

    Only called for failures where the instrument itself is dead — see
    `requires_payment_method_update`. Everything else is either retryable or is better
    served by a payment link alone.
    """
    category = FailureCategory(opportunity.failure_category or FailureCategory.UNKNOWN)

    if not requires_payment_method_update(category, opportunity.failure_code):
        return DispatchOutcome(
            False, MessageStatus.SKIPPED, None, "failure does not require a new payment method"
        )

    channel = _select_channel(customer)

    # --- reasons not to send, recorded rather than silently dropped ---
    skip_reason: str | None = None
    if customer.messaging_opt_out:
        skip_reason = "customer has opted out of messages"
    elif not customer.phone:
        skip_reason = "no contact number on file"
    elif channel is None:
        skip_reason = "no usable messaging channel"

    if skip_reason:
        record = NotificationMessage(
            opportunity_id=opportunity.id,
            attempt_id=attempt.id if attempt else None,
            customer_id=customer.id,
            channel=str(channel or MessageChannel.WHATSAPP),
            provider="none",
            template="payment_method_update",
            recipient_masked=mask_recipient(customer.phone),
            body="",
            action_url=action_url,
            status=MessageStatus.SKIPPED,
            reason=skip_reason,
        )
        session.add(record)
        await session.flush()
        log.info(
            "notification.skipped",
            opportunity_ref=opportunity.opportunity_ref,
            reason=skip_reason,
        )
        return DispatchOutcome(False, MessageStatus.SKIPPED, channel, skip_reason, str(record.id))

    rendered = render_payment_method_update(
        channel=channel,
        merchant=MERCHANT_NAME,
        customer_name=customer.name,
        scenario=Scenario(opportunity.scenario),
        category=category,
        amount_minor=int(opportunity.amount_at_risk_minor),
        action_url=action_url,
        plan_name=plan_name,
    )

    transport = _build_channel(opportunity)
    try:
        result = await transport.send(
            OutboundMessage(
                channel=channel,
                to=customer.phone,
                body=rendered.body,
                template=rendered.template,
                action_url=action_url,
            )
        )
    finally:
        await transport.aclose()

    record = NotificationMessage(
        opportunity_id=opportunity.id,
        attempt_id=attempt.id if attempt else None,
        customer_id=customer.id,
        channel=str(channel),
        provider=result.provider,
        template=rendered.template,
        recipient_masked=mask_recipient(customer.phone),
        body=rendered.body,
        action_url=action_url,
        status=result.status,
        delivered_externally=result.delivered_externally,
        provider_message_id=result.provider_message_id,
        error=result.error,
        sent_at=utcnow(),
        details=result.details,
    )
    session.add(record)
    await session.flush()

    await audit.record(
        session,
        entity_type="recovery_opportunity",
        entity_id=opportunity.opportunity_ref,
        actor=Actor.EXECUTOR,
        action="CUSTOMER_MESSAGED",
        detail={
            "channel": str(channel),
            "provider": result.provider,
            "status": str(result.status),
            "delivered_externally": result.delivered_externally,
            "template": rendered.template,
            "recipient": mask_recipient(customer.phone),
            # Stated explicitly so nobody reads a sent message as a recovery.
            "note": "a delivered message is an action, not recovered revenue",
        },
        simulation_run_id=opportunity.simulation_run_id,
    )

    return DispatchOutcome(
        sent=result.delivered_externally,
        status=result.status,
        channel=channel,
        reason=result.error or "message composed and recorded",
        message_id=str(record.id),
    )
