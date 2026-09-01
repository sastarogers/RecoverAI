"""Customer segment profiles.

Realism here is what makes the benchmark meaningful: an AT_RISK customer must both
*fail more often* and be *genuinely harder to recover*, so a context-blind strategy
cannot match a context-aware one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import CustomerSegment


@dataclass(frozen=True, slots=True)
class SegmentProfile:
    account_age_days: tuple[int, int]
    transaction_count: tuple[int, int]
    success_rate: tuple[float, float]
    aov_multiplier: tuple[float, float]
    checkout_conversion: tuple[float, float]
    #: Hidden driver of recovery success. Correlated with, but not equal to, the
    #: observable historical success rate — so the AI must infer rather than read it.
    reliability: tuple[float, float]
    price_sensitivity: tuple[float, float]
    #: Relative likelihood of being chosen for a transaction.
    activity_weight: float


SEGMENT_PROFILES: dict[CustomerSegment, SegmentProfile] = {
    CustomerSegment.HIGH_VALUE: SegmentProfile(
        account_age_days=(300, 1400),
        transaction_count=(25, 140),
        success_rate=(0.88, 0.98),
        aov_multiplier=(1.6, 3.2),
        checkout_conversion=(0.60, 0.88),
        reliability=(0.75, 0.96),
        price_sensitivity=(0.05, 0.35),
        activity_weight=2.4,
    ),
    CustomerSegment.REGULAR: SegmentProfile(
        account_age_days=(120, 800),
        transaction_count=(8, 45),
        success_rate=(0.80, 0.93),
        aov_multiplier=(0.8, 1.5),
        checkout_conversion=(0.42, 0.72),
        reliability=(0.55, 0.85),
        price_sensitivity=(0.25, 0.60),
        activity_weight=1.5,
    ),
    CustomerSegment.NEW: SegmentProfile(
        account_age_days=(1, 60),
        transaction_count=(0, 4),
        success_rate=(0.55, 0.90),
        aov_multiplier=(0.6, 1.2),
        checkout_conversion=(0.15, 0.50),
        reliability=(0.35, 0.75),
        price_sensitivity=(0.35, 0.80),
        activity_weight=1.0,
    ),
    CustomerSegment.AT_RISK: SegmentProfile(
        account_age_days=(200, 1000),
        transaction_count=(10, 55),
        success_rate=(0.42, 0.70),
        aov_multiplier=(0.7, 1.4),
        checkout_conversion=(0.12, 0.38),
        reliability=(0.18, 0.50),
        price_sensitivity=(0.45, 0.90),
        activity_weight=1.2,
    ),
    CustomerSegment.LOW_ENGAGEMENT: SegmentProfile(
        account_age_days=(90, 900),
        transaction_count=(1, 12),
        success_rate=(0.58, 0.82),
        aov_multiplier=(0.5, 1.0),
        checkout_conversion=(0.08, 0.30),
        reliability=(0.28, 0.62),
        price_sensitivity=(0.40, 0.85),
        activity_weight=0.6,
    ),
}

FIRST_NAMES = (
    "Rahul", "Priya", "Arjun", "Ananya", "Vikram", "Meera", "Karan", "Divya", "Rohan",
    "Sneha", "Aditya", "Kavya", "Siddharth", "Nisha", "Aman", "Pooja", "Varun", "Ishita",
    "Nikhil", "Tanvi", "Manish", "Shreya", "Rajesh", "Aarti", "Sameer", "Neha",
)
LAST_NAMES = (
    "Sharma", "Verma", "Patel", "Reddy", "Nair", "Iyer", "Gupta", "Singh", "Mehta",
    "Joshi", "Rao", "Desai", "Kulkarni", "Banerjee", "Chopra", "Malhotra", "Bose",
)

PRODUCTS = (
    ("Premium Plan", 499_900), ("Pro Annual", 999_900), ("Starter Kit", 149_900),
    ("Growth Bundle", 799_900), ("Analytics Add-on", 249_900), ("Team Seat", 99_900),
    ("Enterprise Trial", 1_999_900), ("Essentials Pack", 49_900), ("Support Plan", 19_900),
)

SUBSCRIPTION_PLANS = (
    ("plan_basic", "Basic Monthly", 19_900), ("plan_standard", "Standard Monthly", 49_900),
    ("plan_pro", "Pro Monthly", 99_900), ("plan_business", "Business Monthly", 249_900),
    ("plan_scale", "Scale Monthly", 499_900), ("plan_pro_annual", "Pro Annual", 999_900),
)
