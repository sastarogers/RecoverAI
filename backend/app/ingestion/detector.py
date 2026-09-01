"""Opportunity detection: NormalizedEvent -> RecoveryOpportunity.

The detector is deliberately source-agnostic. It reads a `NormalizedEvent` and knows
nothing about Razorpay payloads or the simulator's internals.

Idempotency (RULE 6): `dedupe_key` is UNIQUE, so a webhook delivered twice, a webhook
racing a simulator event, or a retried ingestion all resolve to the same opportunity.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.models import (
    CheckoutSession,
    Customer,
    Payment,
    RecoveryOpportunity,
    Subscription,
    SubscriptionEvent,
)
from app.domain.enums import Actor, FailureCategory, OpportunityStatus, Scenario
from app.domain.events import NormalizedEvent
from app.services import audit, refs

log = get_logger("recoverai.detector")

#: How many future billing cycles a retained subscription is credited with when
#: reporting *projected* retention. Reported separately; never recovered revenue.
PROJECTED_RETENTION_CYCLES = 12


@dataclass(slots=True)
class DetectionResult:
    opportunity: RecoveryOpportunity | None
    created: bool
    duplicate: bool
    reason: str


async def detect_opportunity(
    session: AsyncSession,
    event: NormalizedEvent,
    *,
    simulation_run_id: uuid.UUID | None = None,
) -> DetectionResult:
    """Create (or return the existing) revenue opportunity for an event."""

    if not event.opens_opportunity:
        return DetectionResult(None, False, False, "event_does_not_open_opportunity")

    existing = (
        await session.execute(
            select(RecoveryOpportunity).where(RecoveryOpportunity.dedupe_key == event.dedupe_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        log.info(
            "detector.duplicate_suppressed",
            dedupe_key=event.dedupe_key,
        simulation_key=(event.metadata or {}).get("sim_key"),
            opportunity_ref=existing.opportunity_ref,
        )
        return DetectionResult(existing, False, True, "duplicate_dedupe_key")

    customer = (
        await session.execute(
            select(Customer).where(Customer.customer_ref == event.customer_ref)
        )
    ).scalar_one_or_none()
    if customer is None:
        raise NotFoundError(
            f"Unknown customer {event.customer_ref} for event {event.event_id}",
            details={"customer_ref": event.customer_ref, "event_id": event.event_id},
        )

    scenario = event.scenario
    links, amount_at_risk, projected = await _resolve_subject(session, event, scenario)

    opportunity = RecoveryOpportunity(
        opportunity_ref=await refs.next_ref(session, "OPP"),
        scenario=scenario,
        source=event.source,
        simulation_run_id=simulation_run_id,
        customer_id=customer.id,
        amount_at_risk_minor=amount_at_risk,
        currency=event.currency,
        failure_category=(event.failure_category or FailureCategory.UNKNOWN),
        failure_code=event.failure_code,
        reason_code=event.metadata.get("abandonment_reason") if event.metadata else None,
        status=OpportunityStatus.DETECTED,
        projected_retention_minor=projected,
        dedupe_key=event.dedupe_key,
        simulation_key=(event.metadata or {}).get("sim_key"),
        detected_at=event.occurred_at,
        **links,
    )
    try:
        # SAVEPOINT so losing the dedupe race does not roll back the caller's work.
        async with session.begin_nested():
            session.add(opportunity)
            await session.flush()
    except IntegrityError:
        # Lost a race on dedupe_key — the other writer's opportunity is the truth.
        # The savepoint rollback usually evicts it already; expunge only if it lingers.
        if opportunity in session:
            session.expunge(opportunity)
        winner = (
            await session.execute(
                select(RecoveryOpportunity).where(
                    RecoveryOpportunity.dedupe_key == event.dedupe_key
                )
            )
        ).scalar_one()
        return DetectionResult(winner, False, True, "lost_dedupe_race")

    await audit.record(
        session,
        entity_type="recovery_opportunity",
        entity_id=opportunity.opportunity_ref,
        actor=Actor.SYSTEM,
        action="OPPORTUNITY_DETECTED",
        detail={
            "scenario": str(scenario),
            "source": str(event.source),
            "amount_at_risk_minor": amount_at_risk,
            "failure_category": str(opportunity.failure_category),
            "event_id": event.event_id,
        },
        simulation_run_id=simulation_run_id,
    )
    log.info(
        "detector.opportunity_created",
        opportunity_ref=opportunity.opportunity_ref,
        scenario=str(scenario),
        amount_at_risk_minor=amount_at_risk,
    )
    return DetectionResult(opportunity, True, False, "created")


async def _resolve_subject(
    session: AsyncSession, event: NormalizedEvent, scenario: Scenario
) -> tuple[dict, int, int]:
    """Resolve typed links and compute revenue at risk per §7.

    Returns (link kwargs, amount_at_risk_minor, projected_retention_minor).
    """
    if scenario is Scenario.FAILED_PAYMENT:
        payment = await _require(
            session, Payment, Payment.payment_ref == event.payment_ref, event.payment_ref, "payment"
        )
        # Revenue at risk = the amount of the eligible failed payment.
        return {"payment_id": payment.id}, int(payment.amount_minor), 0

    if scenario is Scenario.CHECKOUT_ABANDONMENT:
        checkout = await _require(
            session,
            CheckoutSession,
            CheckoutSession.checkout_ref == event.checkout_ref,
            event.checkout_ref,
            "checkout session",
        )
        # Revenue at risk = value of the abandoned cart.
        return {"checkout_session_id": checkout.id}, int(checkout.cart_value_minor), 0

    if scenario is Scenario.FAILED_SUBSCRIPTION:
        renewal = await _require(
            session,
            SubscriptionEvent,
            SubscriptionEvent.renewal_ref == event.renewal_ref,
            event.renewal_ref,
            "renewal event",
        )
        subscription = (
            await session.execute(
                select(Subscription).where(Subscription.id == renewal.subscription_id)
            )
        ).scalar_one()
        # Revenue at risk = THIS failed renewal only. Future cycles are projected
        # retention, reported separately and never added to recovered revenue (RULE 10).
        at_risk = int(renewal.amount_minor or subscription.amount_minor)
        projected = int(subscription.amount_minor) * PROJECTED_RETENTION_CYCLES
        return (
            {"subscription_id": subscription.id, "subscription_event_id": renewal.id},
            at_risk,
            projected,
        )

    raise NotFoundError(f"Unsupported scenario {scenario}")


async def _require(session: AsyncSession, model, condition, ref_value, label: str):
    obj = (await session.execute(select(model).where(condition))).scalar_one_or_none()
    if obj is None:
        raise NotFoundError(f"Unknown {label} {ref_value}", details={"ref": ref_value})
    return obj
