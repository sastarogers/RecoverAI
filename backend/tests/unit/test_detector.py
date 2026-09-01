"""Detection of all three scenarios, and its idempotency guarantee."""

from datetime import timedelta

import pytest

from app.core.errors import NotFoundError
from app.core.ids import utcnow
from app.domain.enums import FailureCategory, OpportunityStatus, Scenario
from app.ingestion import normalizer_simulator as sim
from app.ingestion.detector import PROJECTED_RETENTION_CYCLES, detect_opportunity
from tests.factories import (
    make_checkout,
    make_customer,
    make_payment,
    make_renewal_failure,
    make_subscription,
)


async def _customer(session, ref="C0001"):
    c = make_customer(ref)
    session.add(c)
    await session.flush()
    return c


async def test_detects_failed_payment_with_full_amount_at_risk(session):
    cust = await _customer(session)
    pay = make_payment(cust, amount_minor=500_000)
    session.add(pay)
    await session.flush()

    event = sim.payment_failed_event(
        payment_ref=pay.payment_ref,
        customer_ref=cust.customer_ref,
        amount_minor=pay.amount_minor,
        method="upi",
        failure_code="BANK_TIMEOUT",
        failure_category=FailureCategory.TEMPORARY,
        occurred_at=utcnow(),
    )
    result = await detect_opportunity(session, event)

    assert result.created is True
    opp = result.opportunity
    assert opp.scenario == Scenario.FAILED_PAYMENT
    assert opp.amount_at_risk_minor == 500_000
    assert opp.status == OpportunityStatus.DETECTED
    assert opp.payment_id == pay.id
    assert opp.projected_retention_minor == 0


async def test_detects_abandoned_checkout_at_cart_value(session):
    cust = await _customer(session)
    chk = make_checkout(cust, cart_value_minor=700_000)
    session.add(chk)
    await session.flush()

    event = sim.checkout_abandoned_event(
        checkout_ref=chk.checkout_ref,
        customer_ref=cust.customer_ref,
        cart_value_minor=chk.cart_value_minor,
        product_count=2,
        occurred_at=utcnow(),
        intended_method="card",
        abandonment_reason="PAYMENT_HESITATION",
    )
    opp = (await detect_opportunity(session, event)).opportunity

    assert opp.scenario == Scenario.CHECKOUT_ABANDONMENT
    assert opp.amount_at_risk_minor == 700_000
    assert opp.failure_category == FailureCategory.ABANDONED
    assert opp.reason_code == "PAYMENT_HESITATION"


async def test_subscription_risk_is_one_renewal_not_the_whole_year(session):
    """RULE 10: future cycles are projected retention, never revenue at risk."""
    cust = await _customer(session)
    sub = make_subscription(cust, amount_minor=99_900)
    session.add(sub)
    await session.flush()
    ren = make_renewal_failure(sub, cust)
    session.add(ren)
    await session.flush()

    event = sim.subscription_failed_event(
        renewal_ref=ren.renewal_ref,
        subscription_ref=sub.subscription_ref,
        customer_ref=cust.customer_ref,
        amount_minor=ren.amount_minor,
        method="card",
        failure_code="INSUFFICIENT_FUNDS",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        occurred_at=utcnow(),
    )
    opp = (await detect_opportunity(session, event)).opportunity

    assert opp.amount_at_risk_minor == 99_900, "only the failed renewal is at risk"
    assert opp.projected_retention_minor == 99_900 * PROJECTED_RETENTION_CYCLES
    assert opp.projected_retention_minor != opp.amount_at_risk_minor


async def test_duplicate_event_creates_only_one_opportunity(session):
    """RULE 6: the same underlying fact delivered twice yields one opportunity."""
    cust = await _customer(session)
    pay = make_payment(cust)
    session.add(pay)
    await session.flush()

    def build():
        return sim.payment_failed_event(
            payment_ref=pay.payment_ref,
            customer_ref=cust.customer_ref,
            amount_minor=pay.amount_minor,
            method="upi",
            failure_code="BANK_TIMEOUT",
            failure_category=FailureCategory.TEMPORARY,
            occurred_at=utcnow(),
        )

    first = await detect_opportunity(session, build())
    second = await detect_opportunity(session, build())

    assert first.created is True and second.created is False
    assert second.duplicate is True
    assert first.opportunity.id == second.opportunity.id


async def test_successful_payment_event_opens_no_opportunity(session):
    """Only revenue-loss events create opportunities."""
    from app.domain.enums import EventType, Source
    from app.domain.events import NormalizedEvent, make_dedupe_key

    event = NormalizedEvent(
        source=Source.SIMULATOR,
        event_type=EventType.PAYMENT_CAPTURED,
        customer_ref="C0001",
        amount_minor=500_000,
        payment_ref="P0009",
        dedupe_key=make_dedupe_key(Source.SIMULATOR, EventType.PAYMENT_CAPTURED, "P0009"),
    )
    result = await detect_opportunity(session, event)
    assert result.opportunity is None and result.created is False


async def test_unknown_customer_is_rejected(session):
    event = sim.payment_failed_event(
        payment_ref="P9999",
        customer_ref="C_NOPE",
        amount_minor=100,
        method="upi",
        failure_code="BANK_TIMEOUT",
        failure_category=FailureCategory.TEMPORARY,
        occurred_at=utcnow(),
    )
    with pytest.raises(NotFoundError):
        await detect_opportunity(session, event)


async def test_refs_are_sequential_and_unique(session):
    cust = await _customer(session)
    refs = []
    for i in range(3):
        pay = make_payment(cust, ref=f"P{i:04d}")
        session.add(pay)
        await session.flush()
        ev = sim.payment_failed_event(
            payment_ref=pay.payment_ref,
            customer_ref=cust.customer_ref,
            amount_minor=pay.amount_minor,
            method="upi",
            failure_code="BANK_TIMEOUT",
            failure_category=FailureCategory.TEMPORARY,
            occurred_at=utcnow() + timedelta(seconds=i),
        )
        refs.append((await detect_opportunity(session, ev)).opportunity.opportunity_ref)
    assert refs == ["OPP0001", "OPP0002", "OPP0003"]
