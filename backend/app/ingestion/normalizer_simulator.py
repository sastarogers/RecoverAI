"""Simulator -> NormalizedEvent.

Deliberately thin: the simulator already speaks the domain vocabulary. Its output is
put through the *same* normalizer boundary as Razorpay's so that no downstream
component can develop a source-specific code path (RULE 8).
"""

from __future__ import annotations

from datetime import datetime

from app.domain.enums import EventType, FailureCategory, Source
from app.domain.events import NormalizedEvent, make_dedupe_key


def payment_failed_event(
    *,
    payment_ref: str,
    customer_ref: str,
    amount_minor: int,
    method: str,
    failure_code: str,
    failure_category: FailureCategory,
    occurred_at: datetime,
    order_ref: str | None = None,
    currency: str = "INR",
    metadata: dict | None = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        source=Source.SIMULATOR,
        event_type=EventType.PAYMENT_FAILED,
        occurred_at=occurred_at,
        customer_ref=customer_ref,
        amount_minor=amount_minor,
        currency=currency,
        payment_ref=payment_ref,
        order_ref=order_ref,
        payment_method=method,
        failure_code=failure_code,
        failure_category=failure_category,
        metadata=metadata or {},
        dedupe_key=make_dedupe_key(Source.SIMULATOR, EventType.PAYMENT_FAILED, payment_ref),
    )


def checkout_abandoned_event(
    *,
    checkout_ref: str,
    customer_ref: str,
    cart_value_minor: int,
    product_count: int,
    occurred_at: datetime,
    intended_method: str | None = None,
    abandonment_reason: str | None = None,
    currency: str = "INR",
    sim_key: str | None = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        source=Source.SIMULATOR,
        event_type=EventType.CHECKOUT_ABANDONED,
        occurred_at=occurred_at,
        customer_ref=customer_ref,
        amount_minor=cart_value_minor,
        currency=currency,
        checkout_ref=checkout_ref,
        payment_method=intended_method,
        failure_category=FailureCategory.ABANDONED,
        failure_code="CART_ABANDONED",
        cart_value_minor=cart_value_minor,
        product_count=product_count,
        metadata={
            k: v
            for k, v in {
                "abandonment_reason": abandonment_reason,
                "sim_key": sim_key,
            }.items()
            if v is not None
        },
        dedupe_key=make_dedupe_key(Source.SIMULATOR, EventType.CHECKOUT_ABANDONED, checkout_ref),
    )


def subscription_failed_event(
    *,
    renewal_ref: str,
    subscription_ref: str,
    customer_ref: str,
    amount_minor: int,
    method: str,
    failure_code: str,
    failure_category: FailureCategory,
    occurred_at: datetime,
    currency: str = "INR",
    metadata: dict | None = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        source=Source.SIMULATOR,
        event_type=EventType.SUBSCRIPTION_PAYMENT_FAILED,
        occurred_at=occurred_at,
        customer_ref=customer_ref,
        amount_minor=amount_minor,
        currency=currency,
        subscription_ref=subscription_ref,
        renewal_ref=renewal_ref,
        payment_method=method,
        failure_code=failure_code,
        failure_category=failure_category,
        metadata=metadata or {},
        dedupe_key=make_dedupe_key(
            Source.SIMULATOR, EventType.SUBSCRIPTION_PAYMENT_FAILED, renewal_ref
        ),
    )
