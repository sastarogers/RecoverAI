"""RecoveryContext — the *only* thing the AI agent ever sees.

Hard rule (§18, RULE 7): this module must never import from
`app.simulation.ground_truth` and must never carry true probabilities, the optimal
action, or any realised outcome. `tests/unit/test_ground_truth_isolation.py`
asserts both the import boundary and the absence of forbidden keys.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import RecoveryAction, Scenario

#: Keys that would leak ground truth. Asserted absent from every serialized context.
FORBIDDEN_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "true_probability",
        "action_success_probs",
        "optimal_action",
        "optimal_probability",
        "ground_truth",
        "ground_truth_outcome",
        "is_recoverable",
        "latent_factors",
        "outcome",
    }
)


class CustomerContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_ref: str
    segment: str
    account_age_days: int
    previous_transaction_count: int
    previous_success_count: int
    previous_failure_count: int
    historical_success_rate: float
    average_order_value: float
    lifetime_value: float
    preferred_payment_method: str | None = None
    previous_recoveries: int = 0


class FailureContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str
    code: str | None = None
    is_retryable_class: bool


class PaymentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str | None = None
    attempt_number: int = 1
    amount_vs_customer_aov: float | None = None


class CheckoutContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cart_value: float
    product_count: int
    minutes_since_abandonment: float
    abandonment_reason: str | None = None
    intended_payment_method: str | None = None
    previous_checkout_count: int = 0
    previous_checkout_conversion_rate: float = 0.0
    has_previously_converted: bool = False


class SubscriptionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float
    billing_cycle: str
    subscription_age_days: int
    renewal_count: int
    previous_successful_renewals: int
    previous_failed_renewals: int
    status: str
    payment_method: str | None = None
    #: Informational only — must never be treated as recoverable revenue (RULE 10).
    projected_retention_value: float = 0.0


class RecoveryHistoryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_count: int = 0
    notification_count: int = 0
    previous_actions: list[str] = Field(default_factory=list)
    previous_outcomes: list[str] = Field(default_factory=list)
    hours_since_detection: float = 0.0


class RecoveryContext(BaseModel):
    """Observable-only decision context."""

    model_config = ConfigDict(extra="forbid")

    opportunity_ref: str
    scenario: Scenario
    amount_at_risk: float
    currency: str = "INR"

    failure: FailureContext
    customer: CustomerContext
    recovery_history: RecoveryHistoryContext
    allowed_actions: list[RecoveryAction]

    payment: PaymentContext | None = None
    checkout: CheckoutContext | None = None
    subscription: SubscriptionContext | None = None

    def to_prompt_dict(self) -> dict:
        """Compact, deterministic dict for the LLM prompt and for the audit snapshot."""
        return json.loads(self.model_dump_json(exclude_none=True))

    def signature(self) -> str:
        """Stable hash over the decision-relevant features.

        Two opportunities with the same signature warrant the same decision, which is
        what makes the LLM decision cache safe (and keeps big simulations affordable).
        """
        payload = {
            "scenario": str(self.scenario),
            "failure": self.failure.category,
            "attempt": self.recovery_history.attempt_count,
            "notifications": self.recovery_history.notification_count,
            "segment": self.customer.segment,
            # bucketed so near-identical cases share a decision
            "success_rate_bucket": round(self.customer.historical_success_rate * 10) / 10,
            "amount_bucket": _amount_bucket(self.amount_at_risk),
            "method": (self.payment.method if self.payment else None)
            or (self.subscription.payment_method if self.subscription else None)
            or (self.checkout.intended_payment_method if self.checkout else None),
            "converted_before": self.checkout.has_previously_converted if self.checkout else None,
            "time_bucket": _time_bucket(self),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.blake2b(blob.encode(), digest_size=16).hexdigest()


def _amount_bucket(amount: float) -> str:
    for edge, label in ((500, "xs"), (1500, "s"), (5000, "m"), (10000, "l"), (25000, "xl")):
        if amount < edge:
            return label
    return "xxl"


def _time_bucket(ctx: RecoveryContext) -> str:
    if ctx.checkout is not None:
        mins = ctx.checkout.minutes_since_abandonment
        for edge, label in ((30, "fresh"), (180, "recent"), (1440, "day"), (4320, "stale")):
            if mins < edge:
                return label
        return "cold"
    hours = ctx.recovery_history.hours_since_detection
    for edge, label in ((1, "fresh"), (6, "recent"), (24, "day"), (72, "stale")):
        if hours < edge:
            return label
    return "cold"
