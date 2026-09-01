"""Simulation configuration — every knob the Simulation Control Centre exposes (§34).

The whole config is persisted on the run row, so an experiment is reproducible from
its stored parameters alone (§57).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.domain.enums import CustomerSegment, FailureCategory, Scenario

#: A realistic Indian SaaS/D2C price ladder. Amounts are sampled from this rather than
#: from a uniform range, so the data looks like real catalogue pricing.
PRICE_LADDER_MINOR: tuple[int, ...] = (
    19_900, 49_900, 99_900, 149_900, 249_900, 499_900, 799_900, 999_900, 1_999_900,
)

DEFAULT_FAILURE_DISTRIBUTION: dict[str, float] = {
    FailureCategory.TEMPORARY: 0.22,
    FailureCategory.NETWORK_ERROR: 0.10,
    FailureCategory.TIMEOUT: 0.12,
    FailureCategory.BANK_DECLINE: 0.14,
    FailureCategory.INSUFFICIENT_FUNDS: 0.16,
    FailureCategory.INVALID_PAYMENT_DETAILS: 0.07,
    FailureCategory.EXPIRED_CARD: 0.06,
    FailureCategory.CUSTOMER_ACTION_REQUIRED: 0.08,
    FailureCategory.PERMANENT: 0.03,
    FailureCategory.UNKNOWN: 0.02,
}

DEFAULT_SEGMENT_DISTRIBUTION: dict[str, float] = {
    CustomerSegment.HIGH_VALUE: 0.10,
    CustomerSegment.REGULAR: 0.45,
    CustomerSegment.NEW: 0.20,
    CustomerSegment.AT_RISK: 0.15,
    CustomerSegment.LOW_ENGAGEMENT: 0.10,
}

DEFAULT_METHOD_DISTRIBUTION: dict[str, float] = {
    "upi": 0.46,
    "card": 0.30,
    "netbanking": 0.12,
    "wallet": 0.08,
    "emi": 0.04,
}

DEFAULT_ABANDONMENT_REASONS: dict[str, float] = {
    "PRICE_CONCERN": 0.28,
    "PAYMENT_FRICTION": 0.22,
    "DISTRACTION": 0.20,
    "COMPARISON_SHOPPING": 0.14,
    "UNEXPECTED_COST": 0.10,
    "TRUST_HESITATION": 0.06,
}


class SimulationConfig(BaseModel):
    """Inputs to a simulation run."""

    model_config = ConfigDict(extra="forbid")

    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    label: str | None = None
    scenarios: list[Scenario] = Field(
        default_factory=lambda: [
            Scenario.FAILED_PAYMENT,
            Scenario.CHECKOUT_ABANDONMENT,
            Scenario.FAILED_SUBSCRIPTION,
        ]
    )

    # --- volumes ---
    num_customers: int = Field(default=300, ge=1, le=20_000)
    num_payments: int = Field(default=600, ge=0, le=50_000)
    num_checkouts: int = Field(default=300, ge=0, le=50_000)
    num_subscriptions: int = Field(default=150, ge=0, le=20_000)

    # --- outcome rates for the *initial* events (§13/§15/§16) ---
    payment_success_rate: float = Field(default=0.70, ge=0.0, le=1.0)
    checkout_completion_rate: float = Field(default=0.70, ge=0.0, le=1.0)
    subscription_renewal_success_rate: float = Field(default=0.85, ge=0.0, le=1.0)

    # --- distributions ---
    failure_distribution: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_FAILURE_DISTRIBUTION)
    )
    segment_distribution: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_SEGMENT_DISTRIBUTION)
    )
    method_distribution: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_METHOD_DISTRIBUTION)
    )
    abandonment_reason_distribution: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_ABANDONMENT_REASONS)
    )

    # --- amounts ---
    amount_min_minor: int = Field(default=19_900, ge=100)
    amount_max_minor: int = Field(default=1_999_900, ge=100)

    #: Share of failed/abandoned items that are genuinely unrecoverable no matter what.
    #: Without this, "always retry" would be an unbeatable strategy and the benchmark
    #: would prove nothing — STOP has to be the right answer sometimes.
    unrecoverable_rate: float = Field(default=0.18, ge=0.0, le=0.9)

    # --- engine ---
    ai_mode: Literal["auto", "llm", "heuristic"] = Field(default_factory=lambda: settings.ai_mode)
    max_attempts: int = Field(default_factory=lambda: settings.policy_max_attempts, ge=1, le=10)
    max_notifications: int = Field(
        default_factory=lambda: settings.policy_max_notifications, ge=0, le=10
    )
    compute_baselines: bool = True

    @field_validator("failure_distribution", "segment_distribution", "method_distribution",
                     "abandonment_reason_distribution")
    @classmethod
    def _non_empty_weights(cls, v: dict[str, float]) -> dict[str, float]:
        if not v or sum(v.values()) <= 0:
            raise ValueError("distribution must contain at least one positive weight")
        if any(w < 0 for w in v.values()):
            raise ValueError("distribution weights must be non-negative")
        return v

    @model_validator(mode="after")
    def _check_amounts(self) -> SimulationConfig:
        if self.amount_max_minor < self.amount_min_minor:
            raise ValueError("amount_max_minor must be >= amount_min_minor")
        if not self.scenarios:
            raise ValueError("at least one scenario must be selected")
        return self

    @property
    def payment_failure_rate(self) -> float:
        return 1.0 - self.payment_success_rate

    @property
    def checkout_abandonment_rate(self) -> float:
        return 1.0 - self.checkout_completion_rate

    @property
    def subscription_failure_rate(self) -> float:
        return 1.0 - self.subscription_renewal_success_rate

    def ladder(self) -> tuple[int, ...]:
        """Price points inside the configured range."""
        inside = tuple(
            p for p in PRICE_LADDER_MINOR if self.amount_min_minor <= p <= self.amount_max_minor
        )
        return inside or (max(self.amount_min_minor, 1),)

    def wants(self, scenario: Scenario) -> bool:
        return scenario in self.scenarios


#: Ready-made presets for the demo (§43/§65).
PRESETS: dict[str, dict] = {
    "demo": {
        "num_customers": 300, "num_payments": 600, "num_checkouts": 300,
        "num_subscriptions": 150, "label": "Demo run",
    },
    "competition": {
        "num_customers": 1000, "num_payments": 2000, "num_checkouts": 1000,
        "num_subscriptions": 500, "label": "Competition demo (§65)",
    },
    "stress": {
        "num_customers": 2000, "num_payments": 5000, "num_checkouts": 2500,
        "num_subscriptions": 1000, "payment_success_rate": 0.55, "label": "High failure stress",
    },
}
