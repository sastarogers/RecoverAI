"""Small object factories for tests. Deliberately independent of the simulator so
that a simulator bug cannot silently mask a pipeline bug.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.core.ids import utcnow
from app.db.models import (
    CheckoutSession,
    Customer,
    Payment,
    RecoveryOpportunity,
    Subscription,
    SubscriptionEvent,
)
from app.domain.enums import (
    BillingCycle,
    CheckoutStatus,
    CustomerSegment,
    FailureCategory,
    OpportunityStatus,
    PaymentStatus,
    Scenario,
    Source,
    SubscriptionEventType,
    SubscriptionStatus,
)


def make_customer(ref: str = "C0001", **kw) -> Customer:
    defaults = dict(
        customer_ref=ref,
        source=Source.SIMULATOR,
        name="Test Customer",
        segment=CustomerSegment.REGULAR,
        account_age_days=365,
        previous_transaction_count=20,
        previous_success_count=18,
        previous_failure_count=2,
        historical_success_rate=0.90,
        average_order_value_minor=420000,
        lifetime_value_minor=8400000,
        preferred_payment_method="upi",
        previous_checkout_count=10,
        previous_checkout_conversions=7,
        previous_checkout_conversion_rate=0.70,
    )
    defaults.update(kw)
    return Customer(**defaults)


def make_payment(customer, ref: str = "P0001", amount_minor: int = 500000, **kw) -> Payment:
    defaults = dict(
        payment_ref=ref,
        customer_id=customer.id,
        amount_minor=amount_minor,
        currency="INR",
        method="upi",
        status=PaymentStatus.FAILED,
        failure_code="BANK_TIMEOUT",
        failure_category=FailureCategory.TEMPORARY,
        source=Source.SIMULATOR,
        occurred_at=utcnow(),
    )
    defaults.update(kw)
    return Payment(**defaults)


def make_checkout(customer, ref: str = "CHK0001", cart_value_minor: int = 700000, **kw):
    defaults = dict(
        checkout_ref=ref,
        customer_id=customer.id,
        cart_value_minor=cart_value_minor,
        product_count=2,
        products=[{"name": "Premium Plan", "price_minor": cart_value_minor}],
        started_at=utcnow() - timedelta(minutes=60),
        abandoned_at=utcnow() - timedelta(minutes=45),
        status=CheckoutStatus.ABANDONED,
        abandonment_reason="PAYMENT_HESITATION",
        payment_method_intended="card",
        source=Source.SIMULATOR,
    )
    defaults.update(kw)
    return CheckoutSession(**defaults)


def make_subscription(customer, ref: str = "SUB0001", amount_minor: int = 99900, **kw):
    defaults = dict(
        subscription_ref=ref,
        customer_id=customer.id,
        plan_id="plan_pro",
        plan_name="Pro Monthly",
        billing_cycle=BillingCycle.MONTHLY,
        amount_minor=amount_minor,
        start_date=date.today() - timedelta(days=210),
        current_renewal_date=date.today(),
        status=SubscriptionStatus.PAST_DUE,
        renewal_count=7,
        previous_successful_renewals=6,
        previous_failed_renewals=1,
        payment_method="card",
        source=Source.SIMULATOR,
    )
    defaults.update(kw)
    return Subscription(**defaults)


def make_renewal_failure(subscription, customer, ref: str = "REN0001", **kw):
    defaults = dict(
        renewal_ref=ref,
        subscription_id=subscription.id,
        customer_id=customer.id,
        cycle_number=8,
        event_type=SubscriptionEventType.RENEWAL_FAILED,
        amount_minor=subscription.amount_minor,
        failure_code="INSUFFICIENT_FUNDS",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        occurred_at=utcnow(),
    )
    defaults.update(kw)
    return SubscriptionEvent(**defaults)


def make_opportunity(customer, ref: str = "OPP0001", amount_minor: int = 500000, **kw):
    defaults = dict(
        opportunity_ref=ref,
        scenario=Scenario.FAILED_PAYMENT,
        source=Source.SIMULATOR,
        customer_id=customer.id,
        amount_at_risk_minor=amount_minor,
        currency="INR",
        failure_category=FailureCategory.TEMPORARY,
        failure_code="BANK_TIMEOUT",
        status=OpportunityStatus.DETECTED,
        dedupe_key=f"SIMULATOR:FAILED_PAYMENT:{ref}",
        detected_at=utcnow(),
    )
    defaults.update(kw)
    return RecoveryOpportunity(**defaults)
