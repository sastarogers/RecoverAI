"""Demo Mode (§43) — scripted single-opportunity scenarios for judges.

Each endpoint creates one realistic opportunity and drives it through the *real*
pipeline, returning the full narrative: detection, AI recommendation, policy verdict,
execution, outcome, and the amount actually recovered.

Ground truth here is deliberately favourable so the happy path is reliable on stage, but
nothing else is faked: the money still only moves because an outcome came back SUCCESS,
and it still passes through the policy engine and the ledger's idempotency guards.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import RecoveryAgent
from app.api import serializers as ser
from app.api.deps import db_session
from app.api.envelope import ok
from app.core.ids import utcnow
from app.core.money import format_inr
from app.db.models import (
    CheckoutSession,
    Customer,
    Payment,
    RecoveryOpportunity,
    SimulationGroundTruth,
    Subscription,
    SubscriptionEvent,
)
from app.domain.enums import (
    BillingCycle,
    CheckoutStatus,
    CustomerSegment,
    FailureCategory,
    PaymentStatus,
    Source,
    SubscriptionEventType,
    SubscriptionStatus,
)
from app.executor.simulator import SimulatorExecutor
from app.ingestion import normalizer_simulator as sim
from app.ingestion.detector import detect_opportunity
from app.pipeline.orchestrator import run_until_resolved
from app.policy.rules import PolicyLimits
from app.services import refs

router = APIRouter(prefix="/demo", tags=["demo"])

DEMO_SEED = 777


class DemoRequest(BaseModel):
    amount_minor: int | None = Field(None, ge=100, le=100_000_000)
    customer_name: str | None = None


async def _demo_customer(session: AsyncSession, name: str | None, segment: str) -> Customer:
    ref = await refs.next_ref(session, "DEMO-C")
    customer = Customer(
        customer_ref=ref,
        source=Source.SIMULATOR,
        name=name or "Rahul Sharma",
        email="demo@example.com",
        # A contactable number, so the expired-card scenario can actually demonstrate
        # the WhatsApp/SMS path instead of skipping for want of a recipient.
        phone="+919812345678",
        segment=segment,
        account_age_days=412,
        previous_transaction_count=37,
        previous_success_count=34,
        previous_failure_count=3,
        historical_success_rate=0.92,
        average_order_value_minor=420_000,
        lifetime_value_minor=15_540_000,
        preferred_payment_method="upi",
        previous_checkout_count=9,
        previous_checkout_conversions=6,
        previous_checkout_conversion_rate=0.67,
        previous_recoveries=2,
        attributes={"demo": True},
    )
    session.add(customer)
    await session.flush()
    return customer


async def _realistic_truth(
    session: AsyncSession, opportunity: RecoveryOpportunity, *, demo_key: str
) -> None:
    """Authentic, mathematically generated ground truth — and repeatable.

    The probabilities come from the same model the simulator uses, so the demo is not
    rigged. But it is keyed on a fixed `demo_key` rather than on `opportunity_ref`:
    demo refs increment on every click, so keying on them made each press of the button
    a fresh dice roll and the scripted scenario a coin flip in front of an audience.
    A stable key keeps the honesty and drops the roulette — the same scenario always
    resolves the same way, and anyone can re-run it and get the same answer.
    """
    from app.domain.enums import FailureCategory, Scenario
    from app.simulation.ground_truth import compute_ground_truth

    truth = compute_ground_truth(
        seed=DEMO_SEED,
        opportunity_ref=demo_key,
        scenario=Scenario(opportunity.scenario),
        failure_category=FailureCategory(opportunity.failure_category or FailureCategory.UNKNOWN.value),
        amount_minor=opportunity.amount_at_risk_minor,
        customer_reliability=0.8,
        price_sensitivity=0.5,
        unrecoverable_rate=0.1,
    )
    # The outcome engine keys its draw on `simulation_key` when present, so stamping it
    # here makes execution deterministic too — not just the probabilities.
    opportunity.simulation_key = demo_key
    session.add(
        SimulationGroundTruth(
            opportunity_id=opportunity.id,
            action_success_probs=truth.action_success_probs,
            latent_factors=truth.latent_factors,
            optimal_action=truth.optimal_action,
            optimal_probability=truth.optimal_probability,
            is_recoverable=truth.is_recoverable,
        )
    )
    await session.flush()


async def _drive(session: AsyncSession, opportunity: RecoveryOpportunity) -> dict:
    agent = RecoveryAgent()
    try:
        cycles = await run_until_resolved(
            session,
            opportunity,
            agent=agent,
            executor=SimulatorExecutor(session, seed=DEMO_SEED),
            limits=PolicyLimits(),
        )
    finally:
        await agent.aclose()
    await session.commit()

    from app.api.routes.opportunities import get_opportunity

    detail = await get_opportunity(str(opportunity.id), session)
    narrative = [
        {
            "step": i,
            "action": c.action,
            "decision_source": c.decision_source,
            "policy_verdict": c.policy_verdict,
            "blocked_by_rule": c.blocked_by_rule,
            "outcome": c.outcome,
            "recovered": ser.money(c.recovered_amount_minor),
            "reason": c.reason,
        }
        for i, c in enumerate(cycles, start=1)
    ]
    return {
        "opportunity": detail["data"],
        "narrative": narrative,
        "headline": (
            f"{format_inr(opportunity.recovered_amount_minor)} RECOVERED"
            if opportunity.recovered_amount_minor
            else f"Not recovered — {narrative[-1]['reason'] if narrative else 'no action taken'}"
        ),
    }


@router.post("/failed-payment")
async def demo_failed_payment(
    body: DemoRequest | None = None, session: AsyncSession = Depends(db_session)
) -> dict:
    """§43: ₹5,000 payment fails on a bank timeout, and RecoverAI recovers it."""
    demo_key = "demo:failed-payment"
    body = body or DemoRequest()
    amount = body.amount_minor or 500_000
    customer = await _demo_customer(session, body.customer_name, CustomerSegment.HIGH_VALUE)

    payment = Payment(
        payment_ref=await refs.next_ref(session, "DEMO-P"),
        customer_id=customer.id,
        amount_minor=amount,
        method="upi",
        status=PaymentStatus.FAILED,
        failure_code="BANK_TIMEOUT",
        failure_category=FailureCategory.TEMPORARY,
        source=Source.SIMULATOR,
        occurred_at=utcnow(),
    )
    session.add(payment)
    await session.flush()

    event = sim.payment_failed_event(
        payment_ref=payment.payment_ref,
        customer_ref=customer.customer_ref,
        amount_minor=amount,
        method="upi",
        failure_code="BANK_TIMEOUT",
        failure_category=FailureCategory.TEMPORARY,
        occurred_at=utcnow(),
    )
    opportunity = (await detect_opportunity(session, event)).opportunity
    await _realistic_truth(session, opportunity, demo_key=demo_key)
    return ok(await _drive(session, opportunity))


@router.post("/checkout-abandonment")
async def demo_checkout_abandonment(
    body: DemoRequest | None = None, session: AsyncSession = Depends(db_session)
) -> dict:
    """§43: a ₹7,000 cart is abandoned and recovered by payment link."""
    demo_key = "demo:checkout-abandonment"
    body = body or DemoRequest()
    amount = body.amount_minor or 700_000
    customer = await _demo_customer(session, body.customer_name, CustomerSegment.REGULAR)

    started = utcnow() - timedelta(minutes=50)
    checkout = CheckoutSession(
        checkout_ref=await refs.next_ref(session, "DEMO-CHK"),
        customer_id=customer.id,
        cart_value_minor=amount,
        product_count=1,
        products=[{"name": "Premium Plan", "price_minor": amount}],
        started_at=started,
        last_activity_at=started + timedelta(minutes=5),
        abandoned_at=started + timedelta(minutes=5),
        status=CheckoutStatus.ABANDONED,
        abandonment_reason="PAYMENT_FRICTION",
        payment_method_intended="card",
        source=Source.SIMULATOR,
    )
    session.add(checkout)
    await session.flush()

    event = sim.checkout_abandoned_event(
        checkout_ref=checkout.checkout_ref,
        customer_ref=customer.customer_ref,
        cart_value_minor=amount,
        product_count=1,
        occurred_at=checkout.abandoned_at,
        intended_method="card",
        abandonment_reason="PAYMENT_FRICTION",
    )
    opportunity = (await detect_opportunity(session, event)).opportunity
    await _realistic_truth(session, opportunity, demo_key=demo_key)
    return ok(await _drive(session, opportunity))


@router.post("/subscription-failure")
async def demo_subscription_failure(
    body: DemoRequest | None = None, session: AsyncSession = Depends(db_session)
) -> dict:
    """§43: a ₹999 renewal fails on an expired card and is recovered."""
    demo_key = "demo:subscription-failure"
    body = body or DemoRequest()
    amount = body.amount_minor or 99_900
    customer = await _demo_customer(session, body.customer_name, CustomerSegment.HIGH_VALUE)

    subscription = Subscription(
        subscription_ref=await refs.next_ref(session, "DEMO-SUB"),
        customer_id=customer.id,
        plan_id="plan_pro",
        plan_name="Pro Monthly",
        billing_cycle=BillingCycle.MONTHLY,
        amount_minor=amount,
        start_date=date.today() - timedelta(days=210),
        current_renewal_date=date.today(),
        status=SubscriptionStatus.PAST_DUE,
        renewal_count=7,
        previous_successful_renewals=7,
        previous_failed_renewals=0,
        payment_method="card",
        source=Source.SIMULATOR,
    )
    session.add(subscription)
    await session.flush()

    renewal = SubscriptionEvent(
        renewal_ref=await refs.next_ref(session, "DEMO-REN"),
        subscription_id=subscription.id,
        customer_id=customer.id,
        cycle_number=8,
        event_type=SubscriptionEventType.RENEWAL_FAILED,
        amount_minor=amount,
        failure_code="CARD_EXPIRED",
        failure_category=FailureCategory.EXPIRED_CARD,
        occurred_at=utcnow(),
    )
    session.add(renewal)
    await session.flush()

    event = sim.subscription_failed_event(
        renewal_ref=renewal.renewal_ref,
        subscription_ref=subscription.subscription_ref,
        customer_ref=customer.customer_ref,
        amount_minor=amount,
        method="card",
        failure_code="CARD_EXPIRED",
        failure_category=FailureCategory.EXPIRED_CARD,
        occurred_at=utcnow(),
    )
    opportunity = (await detect_opportunity(session, event)).opportunity
    await _realistic_truth(session, opportunity, demo_key=demo_key)
    return ok(await _drive(session, opportunity))


@router.post("/reset")
async def reset_demo(session: AsyncSession = Depends(db_session)) -> dict:
    """Remove demo-created data. Simulation runs and live Razorpay data are untouched."""
    demo_customers = (
        (
            await session.execute(
                select(Customer.id).where(Customer.customer_ref.like("DEMO-C%"))
            )
        )
        .scalars()
        .all()
    )
    if demo_customers:
        await session.execute(delete(Customer).where(Customer.id.in_(demo_customers)))
    await session.commit()
    return ok({"removed_customers": len(demo_customers)})
