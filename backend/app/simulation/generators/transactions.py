"""Generation of payments, checkout sessions and subscription renewals.

Each generator emits both the *successful* and the *failed/abandoned* population, so
the dashboard's denominators are real rather than assumed. Only the loss events are
turned into `NormalizedEvent`s and handed to the detector.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from app.core.ids import utcnow
from app.core.rng import derive_rng, weighted_choice
from app.db.models import (
    CheckoutSession,
    Customer,
    Order,
    Payment,
    Subscription,
    SubscriptionEvent,
)
from app.domain.enums import (
    BillingCycle,
    CheckoutStatus,
    FailureCategory,
    OrderStatus,
    PaymentStatus,
    Source,
    SubscriptionEventType,
    SubscriptionStatus,
)
from app.domain.events import NormalizedEvent
from app.ingestion import normalizer_simulator as sim
from app.ingestion.failure_mapping import code_for, supported_categories
from app.simulation.config import SimulationConfig
from app.simulation.generators.customers import GeneratedCustomer
from app.simulation.generators.profiles import PRODUCTS, SUBSCRIPTION_PLANS

#: Events are spread across this window so "time since failure" is meaningful.
HISTORY_WINDOW_DAYS = 14


@dataclass(slots=True)
class RiskEvent:
    """A generated revenue-loss event plus the latent data its ground truth needs."""

    event: NormalizedEvent
    customer_index: int
    reliability: float
    price_sensitivity: float
    hours_since_event: float
    abandonment_reason: str | None = None


@dataclass(slots=True)
class GeneratedBatch:
    orders: list[Order]
    payments: list[Payment]
    checkouts: list[CheckoutSession]
    subscriptions: list[Subscription]
    subscription_events: list[SubscriptionEvent]
    risk_events: list[RiskEvent]
    #: Headline counts for the simulation report.
    stats: dict[str, int]


def _mean(values) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def _pick_customer(rng: random.Random, customers: list[GeneratedCustomer]) -> int:
    """Weight selection by segment activity — high-value customers transact more."""
    weights = {str(i): c.activity_weight for i, c in enumerate(customers)}
    return int(weighted_choice(rng, weights))


def _tilt(value: float, mean_value: float, low: float, span: float) -> float:
    """Mean-preserving tilt.

    Coupling success to a customer's latent reliability is what makes failures cluster
    realistically, but a naive multiplier also *shifts the population rate* away from
    the configured one. Dividing by the population mean keeps the tilt while making the
    control-centre knob mean what it says.
    """
    numerator = low + span * value
    denominator = low + span * mean_value
    return numerator / denominator if denominator else 1.0


def _amount_for(rng: random.Random, config: SimulationConfig, customer: Customer) -> int:
    """Sample from the price ladder, nudged toward the customer's typical order value."""
    ladder = config.ladder()
    aov = int(customer.average_order_value_minor or 149_900)
    weights = {str(p): 1.0 / (1.0 + abs(p - aov) / max(aov, 1)) for p in ladder}
    return int(weighted_choice(rng, weights))


def _failure_for(
    rng: random.Random, config: SimulationConfig, method: str
) -> tuple[FailureCategory, str]:
    """Draw a failure category, restricted to what this payment method can produce."""
    allowed = set(supported_categories(method))
    weights = {k: v for k, v in config.failure_distribution.items() if FailureCategory(k) in allowed}
    if not weights:
        weights = {FailureCategory.UNKNOWN: 1.0}
    category = FailureCategory(weighted_choice(rng, weights))
    return category, code_for(method, category, rng.randint(0, 5))


def generate_payments(
    config: SimulationConfig,
    customers: list[GeneratedCustomer],
    *,
    ref_offset: int = 0,
) -> GeneratedBatch:
    orders: list[Order] = []
    payments: list[Payment] = []
    risks: list[RiskEvent] = []
    now = utcnow()
    succeeded = failed = 0
    mean_reliability = _mean(c.reliability for c in customers)

    for n in range(config.num_payments):
        i = n + 1
        ref_n = ref_offset + i
        rng = derive_rng(config.seed, "payment", i)
        ci = _pick_customer(rng, customers)
        gc = customers[ci]
        cust = gc.model

        amount = _amount_for(rng, config, cust)
        method = weighted_choice(rng, config.method_distribution)
        age_hours = rng.uniform(0.1, HISTORY_WINDOW_DAYS * 24)
        occurred = now - timedelta(hours=age_hours)

        order_ref, payment_ref = f"O{ref_n:05d}", f"P{ref_n:05d}"
        # A customer's own reliability shifts their chance of failing, so failures
        # cluster on the customers who genuinely struggle.
        threshold = config.payment_success_rate * _tilt(
            gc.reliability, mean_reliability, 0.75, 0.45
        )
        is_success = rng.random() < min(threshold, 0.985)

        orders.append(
            Order(
                order_ref=order_ref,
                customer_id=cust.id,
                amount_minor=amount,
                status=OrderStatus.PAID if is_success else OrderStatus.ATTEMPTED,
                source=Source.SIMULATOR,
                placed_at=occurred,
                order_metadata={"generated": True},
            )
        )

        if is_success:
            succeeded += 1
            payments.append(
                Payment(
                    payment_ref=payment_ref, customer_id=cust.id, amount_minor=amount,
                    method=method, status=PaymentStatus.CAPTURED, source=Source.SIMULATOR,
                    occurred_at=occurred, payment_metadata={"order_ref": order_ref},
                )
            )
            continue

        failed += 1
        category, code = _failure_for(rng, config, method)
        payments.append(
            Payment(
                payment_ref=payment_ref, customer_id=cust.id, amount_minor=amount,
                method=method, status=PaymentStatus.FAILED, failure_code=code,
                failure_category=category, source=Source.SIMULATOR, occurred_at=occurred,
                payment_metadata={"order_ref": order_ref},
            )
        )
        risks.append(
            RiskEvent(
                event=sim.payment_failed_event(
                    payment_ref=payment_ref, customer_ref=cust.customer_ref,
                    amount_minor=amount, method=method, failure_code=code,
                    failure_category=category, occurred_at=occurred, order_ref=order_ref,
                    metadata={"sim_key": f"payment:{i}"},
                ),
                customer_index=ci, reliability=gc.reliability,
                price_sensitivity=gc.price_sensitivity, hours_since_event=age_hours,
            )
        )

    return GeneratedBatch(
        orders, payments, [], [], [], risks,
        {"payments_total": config.num_payments, "payments_succeeded": succeeded,
         "payments_failed": failed},
    )


def generate_checkouts(
    config: SimulationConfig,
    customers: list[GeneratedCustomer],
    *,
    ref_offset: int = 0,
) -> GeneratedBatch:
    checkouts: list[CheckoutSession] = []
    risks: list[RiskEvent] = []
    now = utcnow()
    completed = abandoned = 0
    mean_conversion = _mean(
        float(c.model.previous_checkout_conversion_rate or 0.4) for c in customers
    )

    for n in range(config.num_checkouts):
        i = n + 1
        ref_n = ref_offset + i
        rng = derive_rng(config.seed, "checkout", i)
        ci = _pick_customer(rng, customers)
        gc = customers[ci]
        cust = gc.model

        product_count = rng.choices((1, 2, 3, 4), weights=(0.55, 0.26, 0.13, 0.06))[0]
        picked = [rng.choice(PRODUCTS) for _ in range(product_count)]
        cart_value = sum(p[1] for p in picked)
        cart_value = max(config.amount_min_minor, min(cart_value, config.amount_max_minor * 2))

        method = weighted_choice(rng, config.method_distribution)
        age_hours = rng.uniform(0.05, HISTORY_WINDOW_DAYS * 24)
        started = now - timedelta(hours=age_hours + rng.uniform(0.05, 0.5))
        checkout_ref = f"CHK{ref_n:05d}"

        # A customer who historically converts is likelier to convert now.
        conv = float(cust.previous_checkout_conversion_rate or 0.4)
        threshold = config.checkout_completion_rate * _tilt(conv, mean_conversion, 0.70, 0.55)
        is_complete = rng.random() < min(threshold, 0.97)

        if is_complete:
            completed += 1
            checkouts.append(
                CheckoutSession(
                    checkout_ref=checkout_ref, customer_id=cust.id, cart_value_minor=cart_value,
                    product_count=product_count,
                    products=[{"name": p[0], "price_minor": p[1]} for p in picked],
                    started_at=started, last_activity_at=started + timedelta(minutes=4),
                    completed_at=started + timedelta(minutes=5), status=CheckoutStatus.COMPLETED,
                    payment_method_intended=method, source=Source.SIMULATOR,
                )
            )
            continue

        abandoned += 1
        reason = weighted_choice(rng, config.abandonment_reason_distribution)
        abandoned_at = now - timedelta(hours=age_hours)
        checkouts.append(
            CheckoutSession(
                checkout_ref=checkout_ref, customer_id=cust.id, cart_value_minor=cart_value,
                product_count=product_count,
                products=[{"name": p[0], "price_minor": p[1]} for p in picked],
                started_at=started, last_activity_at=abandoned_at, abandoned_at=abandoned_at,
                status=CheckoutStatus.ABANDONED, abandonment_reason=reason,
                payment_method_intended=method, source=Source.SIMULATOR,
            )
        )
        risks.append(
            RiskEvent(
                event=sim.checkout_abandoned_event(
                    checkout_ref=checkout_ref, customer_ref=cust.customer_ref,
                    cart_value_minor=cart_value, product_count=product_count,
                    occurred_at=abandoned_at, intended_method=method,
                    abandonment_reason=reason, sim_key=f"checkout:{i}",
                ),
                customer_index=ci, reliability=gc.reliability,
                price_sensitivity=gc.price_sensitivity, hours_since_event=age_hours,
                abandonment_reason=reason,
            )
        )

    return GeneratedBatch(
        [], [], checkouts, [], [], risks,
        {"checkouts_total": config.num_checkouts, "checkouts_completed": completed,
         "checkouts_abandoned": abandoned},
    )


def generate_subscriptions(
    config: SimulationConfig,
    customers: list[GeneratedCustomer],
    *,
    ref_offset: int = 0,
) -> GeneratedBatch:
    subscriptions: list[Subscription] = []
    events: list[SubscriptionEvent] = []
    risks: list[RiskEvent] = []
    now = utcnow()
    renewed = failed = 0
    mean_reliability = _mean(c.reliability for c in customers)

    for n in range(config.num_subscriptions):
        i = n + 1
        ref_n = ref_offset + i
        rng = derive_rng(config.seed, "subscription", i)
        ci = _pick_customer(rng, customers)
        gc = customers[ci]
        cust = gc.model

        plan_id, plan_name, plan_amount = rng.choice(SUBSCRIPTION_PLANS)
        cycle = BillingCycle.ANNUAL if "Annual" in plan_name else BillingCycle.MONTHLY
        age_days = rng.randint(30, 900)
        renewal_count = max(1, age_days // (365 if cycle is BillingCycle.ANNUAL else 30))
        prior_failures = rng.choices((0, 1, 2, 3), weights=(0.62, 0.24, 0.10, 0.04))[0]
        prior_success = max(0, renewal_count - prior_failures)

        method = weighted_choice(rng, config.method_distribution)
        sub_ref, renewal_ref = f"SUB{ref_n:05d}", f"REN{ref_n:05d}"
        age_hours = rng.uniform(0.1, HISTORY_WINDOW_DAYS * 24)
        occurred = now - timedelta(hours=age_hours)

        threshold = config.subscription_renewal_success_rate * _tilt(
            gc.reliability, mean_reliability, 0.80, 0.35
        )
        is_success = rng.random() < min(threshold, 0.99)

        # The renewal event is created in the same pass as its subscription, so the
        # parent id is assigned here rather than waiting for a flush.
        subscription_id = uuid.uuid4()
        subscriptions.append(
            Subscription(
                id=subscription_id,
                subscription_ref=sub_ref, customer_id=cust.id, plan_id=plan_id,
                plan_name=plan_name, billing_cycle=cycle, amount_minor=plan_amount,
                start_date=date.today() - timedelta(days=age_days),
                current_renewal_date=occurred.date(),
                status=SubscriptionStatus.ACTIVE if is_success else SubscriptionStatus.PAST_DUE,
                renewal_count=renewal_count, previous_successful_renewals=prior_success,
                previous_failed_renewals=prior_failures, payment_method=method,
                source=Source.SIMULATOR,
            )
        )

        if is_success:
            renewed += 1
            events.append(
                SubscriptionEvent(
                    renewal_ref=renewal_ref, subscription_id=subscription_id,
                    customer_id=cust.id, cycle_number=renewal_count + 1,
                    event_type=SubscriptionEventType.RENEWAL_SUCCESS,
                    amount_minor=plan_amount, occurred_at=occurred,
                )
            )
            continue

        failed += 1
        category, code = _failure_for(rng, config, method)
        events.append(
            SubscriptionEvent(
                renewal_ref=renewal_ref, subscription_id=subscription_id,
                customer_id=cust.id, cycle_number=renewal_count + 1,
                event_type=SubscriptionEventType.RENEWAL_FAILED, amount_minor=plan_amount,
                failure_code=code, failure_category=category, occurred_at=occurred,
            )
        )
        risks.append(
            RiskEvent(
                event=sim.subscription_failed_event(
                    renewal_ref=renewal_ref, subscription_ref=sub_ref,
                    customer_ref=cust.customer_ref, amount_minor=plan_amount, method=method,
                    failure_code=code, failure_category=category, occurred_at=occurred,
                    metadata={"sim_key": f"subscription:{i}"},
                ),
                customer_index=ci, reliability=gc.reliability,
                price_sensitivity=gc.price_sensitivity, hours_since_event=age_hours,
            )
        )

    return GeneratedBatch(
        [], [], [], subscriptions, events, risks,
        {"subscriptions_total": config.num_subscriptions, "renewals_succeeded": renewed,
         "renewals_failed": failed},
    )
