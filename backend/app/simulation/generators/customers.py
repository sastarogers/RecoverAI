"""Synthetic customer generation with coherent history (§11).

History is generated *before* current transactions so that when the AI later reads a
customer's success rate, that number genuinely describes the past it was given.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.core.rng import clamp, derive_rng, weighted_choice
from app.db.models import Customer
from app.domain.enums import CustomerSegment, Source
from app.simulation.config import SimulationConfig
from app.simulation.generators.profiles import (
    FIRST_NAMES,
    LAST_NAMES,
    SEGMENT_PROFILES,
    SegmentProfile,
)


@dataclass(slots=True)
class GeneratedCustomer:
    """A customer row plus the latent traits the AI must never see."""

    model: Customer
    #: Hidden: true propensity to complete a recovery. Not stored on the customer row.
    reliability: float
    #: Hidden: responsiveness to a discount incentive.
    price_sensitivity: float
    activity_weight: float


def _between(rng: random.Random, bounds: tuple[float, float]) -> float:
    return rng.uniform(*bounds)


def generate_customers(
    config: SimulationConfig, *, ref_offset: int = 0
) -> list[GeneratedCustomer]:
    """Generate customers for a run.

    `ref_offset` shifts only the human-readable ref so refs stay unique across runs.
    The RNG is keyed on the *logical* index, never the offset — otherwise the same seed
    would produce different customers in a fresh database than in a used one.
    """
    out: list[GeneratedCustomer] = []
    for i in range(1, config.num_customers + 1):
        rng = derive_rng(config.seed, "customer", i)
        segment = CustomerSegment(weighted_choice(rng, config.segment_distribution))
        profile: SegmentProfile = SEGMENT_PROFILES[segment]

        txn_count = rng.randint(*profile.transaction_count)
        # Observable success rate: the *realised* history, which is a noisy read on the
        # customer's true reliability rather than a copy of it.
        target_rate = _between(rng, profile.success_rate)
        success_count = min(txn_count, round(txn_count * target_rate))
        failure_count = txn_count - success_count
        observed_rate = round(success_count / txn_count, 4) if txn_count else 0.0

        base_aov = 149_900
        aov = int(base_aov * _between(rng, profile.aov_multiplier))
        ltv = aov * max(txn_count, 1)

        checkout_count = rng.randint(0, max(2, txn_count * 2))
        conv_rate = _between(rng, profile.checkout_conversion)
        conversions = round(checkout_count * conv_rate)

        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        ref = f"C{ref_offset + i:04d}"
        model = Customer(
            customer_ref=ref,
            source=Source.SIMULATOR,
            name=name,
            email=f"{name.split()[0].lower()}.{i}@example.com",
            phone=f"+9198{rng.randint(10_000_000, 99_999_999)}",
            segment=segment,
            account_age_days=rng.randint(*profile.account_age_days),
            previous_transaction_count=txn_count,
            previous_success_count=success_count,
            previous_failure_count=failure_count,
            historical_success_rate=observed_rate,
            average_order_value_minor=aov,
            lifetime_value_minor=ltv,
            preferred_payment_method=weighted_choice(rng, config.method_distribution),
            previous_checkout_count=checkout_count,
            previous_checkout_conversions=conversions,
            previous_checkout_conversion_rate=round(conv_rate, 4),
            previous_subscription_count=0,
            previous_subscription_failures=0,
            previous_recoveries=rng.randint(0, 3) if txn_count > 10 else 0,
            attributes={"generated": True},
        )

        # Latent reliability is anchored on the segment and only loosely coupled to the
        # observable rate — enough signal to reward good inference, enough noise that
        # copying the observable number is not optimal.
        reliability = clamp(
            0.65 * _between(rng, profile.reliability) + 0.35 * observed_rate
            + rng.gauss(0, 0.06),
            0.02,
            0.98,
        )
        out.append(
            GeneratedCustomer(
                model=model,
                reliability=reliability,
                price_sensitivity=clamp(_between(rng, profile.price_sensitivity), 0.0, 1.0),
                activity_weight=profile.activity_weight,
            )
        )
    return out
