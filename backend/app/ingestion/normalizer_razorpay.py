"""Razorpay webhook payload -> NormalizedEvent.

All Razorpay-specific shape knowledge stops here. Everything downstream sees the same
`NormalizedEvent` the simulator produces, which is what lets one pipeline serve both
sources (RULE 8).

Razorpay amounts are already in paise, so they map to `amount_minor` unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.errors import ValidationError
from app.domain.enums import EventType, FailureCategory, Source
from app.domain.events import NormalizedEvent, make_dedupe_key
from app.ingestion.failure_mapping import categorize

#: Razorpay webhook event name -> internal event type.
EVENT_TYPE_MAP: dict[str, EventType] = {
    "payment.failed": EventType.PAYMENT_FAILED,
    "payment.authorized": EventType.PAYMENT_AUTHORIZED,
    "payment.captured": EventType.PAYMENT_CAPTURED,
    "order.paid": EventType.ORDER_PAID,
    "subscription.charged": EventType.SUBSCRIPTION_RENEWED,
    "subscription.halted": EventType.SUBSCRIPTION_HALTED,
    "subscription.pending": EventType.SUBSCRIPTION_PAYMENT_FAILED,
    "payment_link.paid": EventType.PAYMENT_CAPTURED,
}

SUPPORTED_EVENTS = frozenset(EVENT_TYPE_MAP)


def _ts(value: Any) -> datetime:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    return datetime.now(UTC)


def _entity(payload: dict, name: str) -> dict:
    return (payload.get("payload") or {}).get(name, {}).get("entity") or {}


def normalize(payload: dict, *, razorpay_event_id: str | None = None) -> NormalizedEvent | None:
    """Translate a verified Razorpay webhook body. Returns None for events we ignore."""

    event_name = payload.get("event")
    if not event_name:
        raise ValidationError("Razorpay payload has no `event` field")
    event_type = EVENT_TYPE_MAP.get(event_name)
    if event_type is None:
        return None  # not an event RecoverAI acts on

    payment = _entity(payload, "payment")
    order = _entity(payload, "order")
    subscription = _entity(payload, "subscription")
    payment_link = _entity(payload, "payment_link")

    entity = payment or payment_link or order or subscription
    if not entity:
        raise ValidationError(f"Razorpay {event_name} payload carried no usable entity")

    notes = {**(order.get("notes") or {}), **(entity.get("notes") or {})}

    # RecoverAI stamps these on every recovery artifact it creates, which is what makes
    # an incoming successful payment attributable to the opportunity that caused it.
    attribution = {
        k: notes[k]
        for k in ("recoverai_opportunity_ref", "recoverai_attempt_ref", "recoverai_scenario")
        if k in notes
    }

    customer_ref = (
        notes.get("recoverai_customer_ref")
        or entity.get("customer_id")
        or order.get("customer_id")
        or entity.get("email")
        or "RZP_UNKNOWN"
    )

    failure_category: FailureCategory | None = None
    failure_code: str | None = None
    if event_type is EventType.PAYMENT_FAILED or event_type is EventType.SUBSCRIPTION_PAYMENT_FAILED:
        resolved = categorize(
            entity.get("error_code") or entity.get("error_step"),
            reason=entity.get("error_reason") or entity.get("error_description"),
        )
        failure_category, failure_code = resolved.category, resolved.code

    payment_ref = entity.get("id") if (payment or payment_link) else None
    subject_ref = (
        razorpay_event_id
        or payment_ref
        or order.get("id")
        or subscription.get("id")
        or event_name
    )

    external_ids = {
        k: v
        for k, v in {
            "razorpay_payment_id": payment.get("id") or payment_link.get("id"),
            "razorpay_order_id": order.get("id") or entity.get("order_id"),
            "razorpay_subscription_id": subscription.get("id") or entity.get("subscription_id"),
            "razorpay_event_id": razorpay_event_id,
        }.items()
        if v
    }

    return NormalizedEvent(
        source=Source.RAZORPAY,
        event_type=event_type,
        occurred_at=_ts(payload.get("created_at") or entity.get("created_at")),
        customer_ref=str(customer_ref),
        amount_minor=int(entity.get("amount") or order.get("amount") or 0),
        currency=entity.get("currency") or order.get("currency") or "INR",
        payment_ref=payment_ref,
        order_ref=order.get("id") or entity.get("order_id"),
        subscription_ref=subscription.get("id") or entity.get("subscription_id"),
        payment_method=entity.get("method"),
        failure_code=failure_code,
        failure_category=failure_category,
        external_ids=external_ids,
        attribution=attribution,
        metadata={"razorpay_event": event_name, "notes": notes},
        # Idempotency anchors on Razorpay's own event id when present, so redeliveries
        # of the same event collapse even if the entity appears in several events.
        dedupe_key=make_dedupe_key(Source.RAZORPAY, event_type, str(subject_ref)),
    )
