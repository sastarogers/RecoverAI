"""The single normalized internal event shape.

Razorpay webhooks and the synthetic simulator both produce `NormalizedEvent`.
No component downstream of the normalizer branches on the source except to record it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.ids import short_id, utcnow
from app.domain.enums import EventType, FailureCategory, Scenario, Source


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    event_id: str = Field(default_factory=lambda: short_id("evt"))
    source: Source
    event_type: EventType
    occurred_at: datetime = Field(default_factory=utcnow)

    customer_ref: str
    amount_minor: int = Field(ge=0)
    currency: str = "INR"

    # Scenario-specific subject references (exactly one branch is meaningful)
    payment_ref: str | None = None
    order_ref: str | None = None
    checkout_ref: str | None = None
    subscription_ref: str | None = None
    renewal_ref: str | None = None

    payment_method: str | None = None
    failure_code: str | None = None
    failure_category: FailureCategory | None = None

    cart_value_minor: int | None = None
    product_count: int | None = None

    external_ids: dict[str, Any] = Field(default_factory=dict)
    #: Set on *recovery* payments so a successful payment can be attributed back
    #: to the opportunity that caused it (§40/§41).
    attribution: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    #: Idempotency key. Duplicate deliveries of the same underlying fact collapse here.
    dedupe_key: str

    # --- derived helpers -------------------------------------------------

    @property
    def scenario(self) -> Scenario | None:
        """The recovery scenario this event opens, or None if it opens none."""
        return OPPORTUNITY_OPENING_EVENTS.get(self.event_type)

    @property
    def opens_opportunity(self) -> bool:
        return self.event_type in OPPORTUNITY_OPENING_EVENTS

    @property
    def subject_ref(self) -> str:
        """The reference identifying the thing at risk."""
        return (
            self.renewal_ref
            or self.checkout_ref
            or self.payment_ref
            or self.subscription_ref
            or self.order_ref
            or self.event_id
        )


#: Events that create a revenue opportunity, and the scenario they map to.
OPPORTUNITY_OPENING_EVENTS: dict[EventType, Scenario] = {
    EventType.PAYMENT_FAILED: Scenario.FAILED_PAYMENT,
    EventType.CHECKOUT_ABANDONED: Scenario.CHECKOUT_ABANDONMENT,
    EventType.SUBSCRIPTION_PAYMENT_FAILED: Scenario.FAILED_SUBSCRIPTION,
}

#: Events that can *settle* an opportunity when correctly attributed.
SETTLING_EVENTS: frozenset[EventType] = frozenset(
    {
        EventType.PAYMENT_CAPTURED,
        EventType.ORDER_PAID,
        EventType.CHECKOUT_COMPLETED,
        EventType.SUBSCRIPTION_RENEWED,
    }
)


def make_dedupe_key(source: Source, event_type: EventType, subject_ref: str) -> str:
    return f"{source}:{event_type}:{subject_ref}"
