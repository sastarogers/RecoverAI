"""Razorpay webhook processing (§5).

Two guarantees:

  **Idempotency (RULE 6).** The event is written to `webhook_events` first, under
  UNIQUE(provider, event_id). A redelivery hits that constraint, is marked DUPLICATE and
  returns without touching the pipeline — so a replayed `payment.captured` can never
  settle revenue twice.

  **No faked recoveries.** A Razorpay-sourced recovery settles only when a real webhook
  confirms a successful payment carrying RecoverAI's attribution notes. Nothing here
  marks money recovered on its own authority.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import RecoveryAgent
from app.core.config import settings
from app.core.ids import utcnow
from app.core.logging import get_logger
from app.db.models import (
    Customer,
    Payment,
    RecoveryAttempt,
    RecoveryOpportunity,
    RecoveryOutcome,
    WebhookEvent,
)
from app.domain.enums import (
    Actor,
    EventType,
    EvidenceType,
    ExecutionStatus,
    MessageStatus,
    OpportunityStatus,
    Outcome,
    PaymentStatus,
    Source,
    WebhookStatus,
)
from app.domain.events import SETTLING_EVENTS, NormalizedEvent
from app.executor.razorpay import RazorpayExecutor
from app.ingestion.detector import detect_opportunity
from app.ingestion.normalizer_razorpay import normalize
from app.ledger.settlement import settle_recovery
from app.notifications.service import notify_payment_method_expired
from app.pipeline.orchestrator import run_until_resolved
from app.policy.rules import PolicyLimits
from app.services import audit, refs

log = get_logger("recoverai.webhooks")


@dataclass(slots=True)
class WebhookResult:
    status: WebhookStatus
    message: str
    opportunity_ref: str | None = None
    settled_amount_minor: int = 0
    event_type: str | None = None


async def record_and_process(
    session: AsyncSession,
    *,
    payload: dict,
    raw_event_id: str | None,
    signature_valid: bool,
) -> WebhookResult:
    """Persist the delivery, then process it exactly once."""
    event_id = raw_event_id or _fallback_event_id(payload)
    event_name = payload.get("event")

    record = WebhookEvent(
        provider="razorpay",
        event_id=event_id,
        event_type=event_name,
        signature_valid=signature_valid,
        payload=payload,
        processing_status=WebhookStatus.RECEIVED,
        received_at=utcnow(),
    )
    try:
        # SAVEPOINT, not a plain flush: a duplicate must undo only this insert. A bare
        # session.rollback() here would discard everything else in the transaction —
        # including a settlement performed earlier in the same unit of work.
        async with session.begin_nested():
            session.add(record)
            await session.flush()
    except IntegrityError:
        # UNIQUE(provider, event_id) tripped: we have seen this delivery already.
        # The savepoint rollback usually evicts it already; expunge only if it lingers.
        if record in session:
            session.expunge(record)
        # `event` is reserved by structlog; use razorpay_event.
        log.info("webhook.duplicate_ignored", event_id=event_id, razorpay_event=event_name)
        return WebhookResult(
            WebhookStatus.DUPLICATE,
            "Event already processed; ignored to prevent double counting",
            event_type=event_name,
        )

    if not signature_valid:
        record.processing_status = WebhookStatus.INVALID
        record.error = "signature verification failed"
        record.processed_at = utcnow()
        return WebhookResult(WebhookStatus.INVALID, "Invalid signature", event_type=event_name)

    try:
        event = normalize(payload, razorpay_event_id=event_id)
    except Exception as exc:
        record.processing_status = WebhookStatus.FAILED
        record.error = f"{type(exc).__name__}: {exc}"
        record.processed_at = utcnow()
        return WebhookResult(WebhookStatus.FAILED, record.error, event_type=event_name)

    if event is None:
        record.processing_status = WebhookStatus.PROCESSED
        record.processed_at = utcnow()
        return WebhookResult(
            WebhookStatus.PROCESSED, "Event acknowledged but not actionable", event_type=event_name
        )

    result = await _dispatch(session, event, record)
    record.processing_status = result.status
    record.processed_at = utcnow()
    return result


async def _dispatch(
    session: AsyncSession, event: NormalizedEvent, record: WebhookEvent
) -> WebhookResult:
    await audit.record(
        session,
        entity_type="webhook_event",
        entity_id=record.event_id,
        actor=Actor.WEBHOOK,
        action=f"WEBHOOK_{event.event_type}",
        detail={"source": str(event.source), "amount_minor": event.amount_minor},
    )

    if event.event_type in SETTLING_EVENTS:
        return await _settle_from_event(session, event, record)

    if event.opens_opportunity:
        return await _open_opportunity(session, event, record)

    return WebhookResult(
        WebhookStatus.PROCESSED, "Event recorded", event_type=str(event.event_type)
    )


async def _open_opportunity(
    session: AsyncSession, event: NormalizedEvent, record: WebhookEvent
) -> WebhookResult:
    """A live failure becomes an opportunity on the same pipeline as a simulated one."""
    customer = await _ensure_customer(session, event)
    payment = await _ensure_payment(session, event, customer)

    normalized = event.model_copy(update={"payment_ref": payment.payment_ref})
    detection = await detect_opportunity(session, normalized)
    if detection.opportunity is None:
        return WebhookResult(
            WebhookStatus.PROCESSED, detection.reason, event_type=str(event.event_type)
        )

    record.opportunity_id = detection.opportunity.id

    # --- Run the real pipeline so a live failure does not stall at DETECTED ---
    #
    # This runs the *same* AI agent, policy engine and orchestrator the simulator uses,
    # but with the Razorpay executor, which can only create a payable artifact and return
    # PENDING. Nothing here can mark revenue recovered: that requires an inbound
    # payment.captured / order.paid webhook carrying this opportunity's attribution
    # (§23, RULE 2). The simulator is deliberately not reachable from this path.
    if detection.created:
        opportunity = detection.opportunity
        if not settings.razorpay_configured:
            # Without credentials there is no artifact to create, and inventing an
            # outcome is exactly what must not happen. Leave it detected and say so.
            await audit.record(
                session,
                entity_type="recovery_opportunity",
                entity_id=opportunity.opportunity_ref,
                actor=Actor.SYSTEM,
                action="RECOVERY_DEFERRED",
                detail={
                    "reason": "Razorpay is not configured; no recovery artifact can be created",
                    "status": str(opportunity.status),
                },
            )
            log.info(
                "webhook.recovery_deferred",
                opportunity_ref=opportunity.opportunity_ref,
                reason="razorpay_not_configured",
            )

            # Telling someone their card has expired does not depend on a payment
            # gateway. When the instrument itself is dead, the message is worth sending
            # on its own — it just carries no link to pay.
            try:
                dispatch = await notify_payment_method_expired(
                    session, opportunity=opportunity, customer=customer
                )
                # This path sends outside the orchestrator, which is what normally keeps
                # the counter. Increment here so notification-fatigue limits still see
                # the contact, and the opportunity page does not report "0 messages sent"
                # next to a message it is displaying.
                if dispatch.status is not MessageStatus.SKIPPED:
                    opportunity.notification_count += 1
            except Exception:
                log.exception(
                    "webhook.messaging_failed",
                    opportunity_ref=opportunity.opportunity_ref,
                )
        else:
            try:
                agent = RecoveryAgent()
                try:
                    await run_until_resolved(
                        session,
                        opportunity,
                        agent=agent,
                        executor=RazorpayExecutor(session),
                        limits=PolicyLimits(),
                        customer=customer,
                    )
                finally:
                    await agent.aclose()

                log.info(
                    "webhook.pipeline_completed",
                    opportunity_ref=opportunity.opportunity_ref,
                    status=str(opportunity.status),
                    # Always 0 here by construction; settlement happens on a later webhook.
                    recovered=opportunity.recovered_amount_minor or 0,
                )
            except Exception:
                log.exception(
                    "webhook.pipeline_error",
                    opportunity_ref=opportunity.opportunity_ref,
                )
                # The opportunity exists and is auditable; pipeline failure is non-fatal.

    return WebhookResult(
        WebhookStatus.PROCESSED,
        "Opportunity created" if detection.created else "Opportunity already existed",
        opportunity_ref=detection.opportunity.opportunity_ref,
        event_type=str(event.event_type),
    )


async def _settle_from_event(
    session: AsyncSession, event: NormalizedEvent, record: WebhookEvent
) -> WebhookResult:
    """A successful payment settles an opportunity only with valid attribution (§40)."""
    opportunity_ref = event.attribution.get("recoverai_opportunity_ref")
    attempt_ref = event.attribution.get("recoverai_attempt_ref")

    if not opportunity_ref:
        # A perfectly normal payment that RecoverAI did not cause. Recording an
        # unrelated purchase as recovered revenue is exactly what §40 forbids.
        return WebhookResult(
            WebhookStatus.PROCESSED,
            "Successful payment with no RecoverAI attribution; not counted as recovery",
            event_type=str(event.event_type),
        )

    opportunity = (
        await session.execute(
            select(RecoveryOpportunity).where(
                RecoveryOpportunity.opportunity_ref == opportunity_ref
            )
        )
    ).scalar_one_or_none()
    if opportunity is None:
        return WebhookResult(
            WebhookStatus.FAILED,
            f"Attributed opportunity {opportunity_ref} not found",
            event_type=str(event.event_type),
        )

    record.opportunity_id = opportunity.id

    stmt = select(RecoveryAttempt).where(RecoveryAttempt.opportunity_id == opportunity.id)
    if attempt_ref:
        stmt = stmt.where(RecoveryAttempt.attempt_ref == attempt_ref)
    attempt = (
        await session.execute(stmt.order_by(RecoveryAttempt.attempt_number.desc()).limit(1))
    ).scalar_one_or_none()
    if attempt is None:
        return WebhookResult(
            WebhookStatus.FAILED,
            f"No recovery attempt found for {opportunity_ref}",
            event_type=str(event.event_type),
        )

    if opportunity.status == OpportunityStatus.RECOVERED:
        return WebhookResult(
            WebhookStatus.PROCESSED,
            "Opportunity already recovered; no further settlement",
            opportunity_ref=opportunity_ref,
            event_type=str(event.event_type),
        )

    evidence_ref = event.external_ids.get("razorpay_payment_id") or event.event_id
    existing_outcome = (
        await session.execute(
            select(RecoveryOutcome).where(RecoveryOutcome.attempt_id == attempt.id)
        )
    ).scalar_one_or_none()

    if existing_outcome is None:
        outcome = RecoveryOutcome(
            attempt_id=attempt.id,
            opportunity_id=opportunity.id,
            outcome=Outcome.SUCCESS,
            # Never trust the webhook's amount over what was actually at risk.
            realized_amount_minor=min(
                int(event.amount_minor or 0) or int(opportunity.amount_at_risk_minor),
                int(opportunity.amount_at_risk_minor),
            ),
            evidence_type=EvidenceType.RAZORPAY_WEBHOOK,
            evidence_ref=evidence_ref,
            observed_at=utcnow(),
            raw={"razorpay_event": event.metadata.get("razorpay_event")},
        )
        session.add(outcome)
        await session.flush()
    else:
        outcome = existing_outcome

    attempt.execution_status = ExecutionStatus.EXECUTED
    if opportunity.status not in (OpportunityStatus.SUCCESS, OpportunityStatus.RECOVERED):
        from app.domain.state_machine import transition

        if OpportunityStatus(opportunity.status) is OpportunityStatus.EXECUTING:
            transition(opportunity, OpportunityStatus.SUCCESS)

    settlement = await settle_recovery(
        session, opportunity=opportunity, attempt=attempt, outcome=outcome
    )
    return WebhookResult(
        WebhookStatus.PROCESSED,
        "Recovery settled" if settlement.settled else settlement.reason,
        opportunity_ref=opportunity_ref,
        settled_amount_minor=settlement.recovered_amount_minor,
        event_type=str(event.event_type),
    )


async def _ensure_customer(session: AsyncSession, event: NormalizedEvent) -> Customer:
    customer = (
        await session.execute(
            select(Customer).where(Customer.customer_ref == event.customer_ref)
        )
    ).scalar_one_or_none()
    if customer is not None:
        return customer

    # A live customer RecoverAI has not seen before starts with an empty history rather
    # than invented statistics. Seeding a stranger with a plausible-looking 83% success
    # rate makes the AI reason over fiction and shows fabricated numbers on the
    # opportunity page — the observable-context contract means the model is told what is
    # actually known, including when that is nothing.
    contact = (event.metadata or {}).get("contact") or {}
    customer = Customer(
        customer_ref=event.customer_ref,
        source=Source.RAZORPAY,
        name=contact.get("name") or event.customer_ref,
        email=contact.get("email"),
        # Needed to reach the customer when their payment method is dead. Observed from
        # the gateway payload, not invented.
        phone=contact.get("phone"),
        segment="NEW",
        account_age_days=0,
        historical_success_rate=0.0,
        preferred_payment_method=event.payment_method,
        external_id=event.customer_ref,
        attributes={"created_from_webhook": True},
    )
    session.add(customer)
    await session.flush()
    return customer


async def _ensure_payment(
    session: AsyncSession, event: NormalizedEvent, customer: Customer
) -> Payment:
    external_id = event.external_ids.get("razorpay_payment_id") or event.payment_ref
    existing = (
        await session.execute(select(Payment).where(Payment.external_id == external_id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    payment = Payment(
        payment_ref=await refs.next_ref(session, "RZP-P"),
        customer_id=customer.id,
        amount_minor=int(event.amount_minor or 0),
        currency=event.currency,
        method=event.payment_method,
        status=(
            PaymentStatus.FAILED
            if event.event_type is EventType.PAYMENT_FAILED
            else PaymentStatus.CAPTURED
        ),
        failure_code=event.failure_code,
        failure_category=event.failure_category,
        source=Source.RAZORPAY,
        external_id=external_id,
        occurred_at=event.occurred_at,
        payment_metadata={"razorpay": event.external_ids},
    )
    session.add(payment)
    await session.flush()
    return payment


def _fallback_event_id(payload: dict) -> str:
    """Razorpay sends `x-razorpay-event-id`; fall back to a stable payload identity."""
    import hashlib
    import json

    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(blob).hexdigest()[:32]}"
