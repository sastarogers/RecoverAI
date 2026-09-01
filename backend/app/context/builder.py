"""Context builder — assembles the observable-only view handed to the AI.

RESTRICTED BOUNDARY. This module must not import from `app.simulation.ground_truth`
or `app.simulation.outcome`, and must not read `simulation_ground_truth`. It sees the
same information a real merchant would have at decision time and nothing more (§18).
"""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.ids import utcnow
from app.core.money import to_major
from app.db.models import (
    CheckoutSession,
    Customer,
    Payment,
    RecoveryAttempt,
    RecoveryOpportunity,
    Subscription,
)
from app.domain.context import (
    CheckoutContext,
    CustomerContext,
    FailureContext,
    PaymentContext,
    RecoveryContext,
    RecoveryHistoryContext,
    SubscriptionContext,
)
from app.domain.enums import (
    NON_RETRYABLE_CATEGORIES,
    SCENARIO_ACTIONS,
    FailureCategory,
    Scenario,
)


async def build_context(
    session: AsyncSession,
    opportunity: RecoveryOpportunity,
    *,
    excluded_actions: set[str] | None = None,
    customer: Customer | None = None,
) -> RecoveryContext:
    """Assemble the decision context for one opportunity.

    `excluded_actions` carries policy feedback: an action the policy engine has already
    refused for this opportunity is not offered again, so a blocked recommendation makes
    the next decision better-informed instead of repeating itself.
    """
    if customer is None:
        customer = (
            await session.execute(select(Customer).where(Customer.id == opportunity.customer_id))
        ).scalar_one()

    attempts = (
        (
            await session.execute(
                select(RecoveryAttempt)
                .options(selectinload(RecoveryAttempt.outcome))
                .where(RecoveryAttempt.opportunity_id == opportunity.id)
                .order_by(RecoveryAttempt.attempt_number)
            )
        )
        .scalars()
        .all()
    )

    category = FailureCategory(opportunity.failure_category or FailureCategory.UNKNOWN)
    scenario = Scenario(opportunity.scenario)
    amount = to_major(opportunity.amount_at_risk_minor)
    hours_since = max(
        0.0, (utcnow() - _aware(opportunity.detected_at)).total_seconds() / 3600.0
    )

    ctx_kwargs: dict = {
        "opportunity_ref": opportunity.opportunity_ref,
        "scenario": scenario,
        "amount_at_risk": amount,
        "currency": opportunity.currency,
        "failure": FailureContext(
            category=str(category),
            code=opportunity.failure_code,
            is_retryable_class=category not in NON_RETRYABLE_CATEGORIES,
        ),
        "customer": _customer_context(customer),
        "recovery_history": RecoveryHistoryContext(
            attempt_count=opportunity.attempt_count,
            notification_count=opportunity.notification_count,
            previous_actions=[str(a.action) for a in attempts],
            previous_outcomes=[
                str(a.outcome.outcome) if a.outcome else "PENDING" for a in attempts
            ],
            hours_since_detection=round(hours_since, 3),
        ),
        "allowed_actions": _offer_actions(scenario, excluded_actions),
    }

    if scenario is Scenario.FAILED_PAYMENT and opportunity.payment_id:
        payment = (
            await session.execute(select(Payment).where(Payment.id == opportunity.payment_id))
        ).scalar_one()
        aov = float(customer.average_order_value_minor or 0)
        ctx_kwargs["payment"] = PaymentContext(
            method=payment.method,
            attempt_number=opportunity.attempt_count + 1,
            amount_vs_customer_aov=(
                round(opportunity.amount_at_risk_minor / aov, 3) if aov else None
            ),
        )

    elif scenario is Scenario.CHECKOUT_ABANDONMENT and opportunity.checkout_session_id:
        checkout = (
            await session.execute(
                select(CheckoutSession).where(
                    CheckoutSession.id == opportunity.checkout_session_id
                )
            )
        ).scalar_one()
        abandoned_at = _aware(checkout.abandoned_at or checkout.started_at)
        ctx_kwargs["checkout"] = CheckoutContext(
            cart_value=to_major(checkout.cart_value_minor),
            product_count=checkout.product_count,
            minutes_since_abandonment=round(
                max(0.0, (utcnow() - abandoned_at).total_seconds() / 60.0), 1
            ),
            abandonment_reason=checkout.abandonment_reason,
            intended_payment_method=checkout.payment_method_intended,
            previous_checkout_count=customer.previous_checkout_count,
            previous_checkout_conversion_rate=float(
                customer.previous_checkout_conversion_rate or 0
            ),
            has_previously_converted=bool(customer.previous_checkout_conversions),
        )

    elif scenario is Scenario.FAILED_SUBSCRIPTION and opportunity.subscription_id:
        subscription = (
            await session.execute(
                select(Subscription).where(Subscription.id == opportunity.subscription_id)
            )
        ).scalar_one()
        age_days = (utcnow().date() - subscription.start_date).days
        ctx_kwargs["subscription"] = SubscriptionContext(
            amount=to_major(subscription.amount_minor),
            billing_cycle=str(subscription.billing_cycle),
            subscription_age_days=max(0, age_days),
            renewal_count=subscription.renewal_count,
            previous_successful_renewals=subscription.previous_successful_renewals,
            previous_failed_renewals=subscription.previous_failed_renewals,
            status=str(subscription.status),
            payment_method=subscription.payment_method,
            # Shown to the model as context on how much retention is at stake, and
            # explicitly labelled as projected — it is never recoverable revenue.
            projected_retention_value=to_major(opportunity.projected_retention_minor),
        )

    return RecoveryContext(**ctx_kwargs)


def _offer_actions(scenario: Scenario, excluded: set[str] | None) -> list:
    """Everything valid for the scenario, minus what policy has already refused.

    STOP always remains available — the agent must always be able to say "stop".
    """
    from app.domain.enums import RecoveryAction

    actions = list(SCENARIO_ACTIONS[scenario])
    if not excluded:
        return actions
    remaining = [a for a in actions if str(a) not in excluded]
    if RecoveryAction.STOP not in remaining:
        remaining.append(RecoveryAction.STOP)
    return remaining


def _customer_context(customer: Customer) -> CustomerContext:
    return CustomerContext(
        customer_ref=customer.customer_ref,
        segment=str(customer.segment),
        account_age_days=customer.account_age_days,
        previous_transaction_count=customer.previous_transaction_count,
        previous_success_count=customer.previous_success_count,
        previous_failure_count=customer.previous_failure_count,
        historical_success_rate=float(customer.historical_success_rate or 0),
        average_order_value=to_major(customer.average_order_value_minor),
        lifetime_value=to_major(customer.lifetime_value_minor),
        preferred_payment_method=customer.preferred_payment_method,
        previous_recoveries=customer.previous_recoveries,
    )


def _aware(dt):
    """Normalize naive timestamps (SQLite round-trips) to UTC."""

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
