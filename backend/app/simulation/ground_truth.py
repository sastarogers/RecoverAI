"""Hidden ground truth (§17).

RESTRICTED MODULE. Nothing in `app.context`, `app.ai` or `app.policy` may import this,
and no value computed here may reach the AI before it decides. The correct order is:

    observable context -> AI decision -> policy verdict -> execution -> ground truth

`tests/unit/test_ground_truth_isolation.py` enforces the import boundary statically.

For every opportunity the simulator computes a *true* success probability for each
available action, driven by latent factors the AI cannot observe. That is what makes
the evaluation objective: a strategy that reads context well earns more revenue than
one that retries blindly, and the difference is measurable rather than asserted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.rng import clamp, derive_rng
from app.domain.enums import FailureCategory as FC
from app.domain.enums import RecoveryAction as A
from app.domain.enums import Scenario

#: Base success probability per scenario / failure category / action.
#: Read a row as: "given this failure, how often does this action actually work?"
BASE_PROBS: dict[Scenario, dict[FC, dict[A, float]]] = {
    Scenario.FAILED_PAYMENT: {
        FC.TEMPORARY: {
            A.IMMEDIATE_RETRY: 0.45, A.DELAYED_RETRY: 0.82, A.PAYMENT_LINK: 0.61,
            A.ALTERNATE_PAYMENT_METHOD: 0.55, A.CUSTOMER_NOTIFICATION: 0.30,
        },
        FC.NETWORK_ERROR: {
            A.IMMEDIATE_RETRY: 0.64, A.DELAYED_RETRY: 0.76, A.PAYMENT_LINK: 0.56,
            A.ALTERNATE_PAYMENT_METHOD: 0.50, A.CUSTOMER_NOTIFICATION: 0.26,
        },
        FC.TIMEOUT: {
            A.IMMEDIATE_RETRY: 0.50, A.DELAYED_RETRY: 0.78, A.PAYMENT_LINK: 0.58,
            A.ALTERNATE_PAYMENT_METHOD: 0.52, A.CUSTOMER_NOTIFICATION: 0.28,
        },
        FC.BANK_DECLINE: {
            A.IMMEDIATE_RETRY: 0.12, A.DELAYED_RETRY: 0.34, A.PAYMENT_LINK: 0.45,
            A.ALTERNATE_PAYMENT_METHOD: 0.61, A.CUSTOMER_NOTIFICATION: 0.24,
        },
        FC.INSUFFICIENT_FUNDS: {
            A.IMMEDIATE_RETRY: 0.07, A.DELAYED_RETRY: 0.46, A.PAYMENT_LINK: 0.54,
            A.ALTERNATE_PAYMENT_METHOD: 0.36, A.CUSTOMER_NOTIFICATION: 0.41,
        },
        FC.INVALID_PAYMENT_DETAILS: {
            A.IMMEDIATE_RETRY: 0.02, A.DELAYED_RETRY: 0.03, A.PAYMENT_LINK: 0.49,
            A.ALTERNATE_PAYMENT_METHOD: 0.53, A.CUSTOMER_NOTIFICATION: 0.34,
        },
        FC.EXPIRED_CARD: {
            A.IMMEDIATE_RETRY: 0.01, A.DELAYED_RETRY: 0.02, A.PAYMENT_LINK: 0.51,
            A.ALTERNATE_PAYMENT_METHOD: 0.59, A.CUSTOMER_NOTIFICATION: 0.38,
        },
        FC.CUSTOMER_ACTION_REQUIRED: {
            A.IMMEDIATE_RETRY: 0.09, A.DELAYED_RETRY: 0.17, A.PAYMENT_LINK: 0.63,
            A.ALTERNATE_PAYMENT_METHOD: 0.44, A.CUSTOMER_NOTIFICATION: 0.49,
        },
        FC.PERMANENT: {
            A.IMMEDIATE_RETRY: 0.01, A.DELAYED_RETRY: 0.01, A.PAYMENT_LINK: 0.04,
            A.ALTERNATE_PAYMENT_METHOD: 0.06, A.CUSTOMER_NOTIFICATION: 0.03,
        },
        FC.UNKNOWN: {
            A.IMMEDIATE_RETRY: 0.20, A.DELAYED_RETRY: 0.41, A.PAYMENT_LINK: 0.45,
            A.ALTERNATE_PAYMENT_METHOD: 0.42, A.CUSTOMER_NOTIFICATION: 0.25,
        },
    },
    Scenario.CHECKOUT_ABANDONMENT: {
        FC.ABANDONED: {
            A.REMINDER: 0.24, A.PAYMENT_LINK: 0.39, A.CHECKOUT_RESUME: 0.43,
            A.DISCOUNT_INCENTIVE: 0.52, A.ALTERNATE_PAYMENT_METHOD: 0.30,
            A.CUSTOMER_NOTIFICATION: 0.19,
        },
    },
    Scenario.FAILED_SUBSCRIPTION: {
        FC.TEMPORARY: {
            A.RETRY_SUBSCRIPTION: 0.56, A.DELAYED_RETRY: 0.80, A.PAYMENT_UPDATE_REQUEST: 0.44,
            A.PAYMENT_LINK: 0.55, A.CUSTOMER_NOTIFICATION: 0.31,
            A.ALTERNATE_PAYMENT_METHOD: 0.42, A.GRACE_PERIOD: 0.38,
        },
        FC.NETWORK_ERROR: {
            A.RETRY_SUBSCRIPTION: 0.62, A.DELAYED_RETRY: 0.74, A.PAYMENT_UPDATE_REQUEST: 0.40,
            A.PAYMENT_LINK: 0.52, A.CUSTOMER_NOTIFICATION: 0.27,
            A.ALTERNATE_PAYMENT_METHOD: 0.40, A.GRACE_PERIOD: 0.34,
        },
        FC.TIMEOUT: {
            A.RETRY_SUBSCRIPTION: 0.53, A.DELAYED_RETRY: 0.77, A.PAYMENT_UPDATE_REQUEST: 0.41,
            A.PAYMENT_LINK: 0.53, A.CUSTOMER_NOTIFICATION: 0.29,
            A.ALTERNATE_PAYMENT_METHOD: 0.41, A.GRACE_PERIOD: 0.36,
        },
        FC.BANK_DECLINE: {
            A.RETRY_SUBSCRIPTION: 0.13, A.DELAYED_RETRY: 0.33, A.PAYMENT_UPDATE_REQUEST: 0.58,
            A.PAYMENT_LINK: 0.47, A.CUSTOMER_NOTIFICATION: 0.26,
            A.ALTERNATE_PAYMENT_METHOD: 0.56, A.GRACE_PERIOD: 0.31,
        },
        FC.INSUFFICIENT_FUNDS: {
            A.RETRY_SUBSCRIPTION: 0.09, A.DELAYED_RETRY: 0.49, A.PAYMENT_UPDATE_REQUEST: 0.43,
            A.PAYMENT_LINK: 0.51, A.CUSTOMER_NOTIFICATION: 0.39,
            A.ALTERNATE_PAYMENT_METHOD: 0.40, A.GRACE_PERIOD: 0.55,
        },
        FC.INVALID_PAYMENT_DETAILS: {
            A.RETRY_SUBSCRIPTION: 0.02, A.DELAYED_RETRY: 0.03, A.PAYMENT_UPDATE_REQUEST: 0.70,
            A.PAYMENT_LINK: 0.52, A.CUSTOMER_NOTIFICATION: 0.36,
            A.ALTERNATE_PAYMENT_METHOD: 0.49, A.GRACE_PERIOD: 0.14,
        },
        FC.EXPIRED_CARD: {
            A.RETRY_SUBSCRIPTION: 0.01, A.DELAYED_RETRY: 0.02, A.PAYMENT_UPDATE_REQUEST: 0.74,
            A.PAYMENT_LINK: 0.55, A.CUSTOMER_NOTIFICATION: 0.40,
            A.ALTERNATE_PAYMENT_METHOD: 0.51, A.GRACE_PERIOD: 0.16,
        },
        FC.CUSTOMER_ACTION_REQUIRED: {
            A.RETRY_SUBSCRIPTION: 0.08, A.DELAYED_RETRY: 0.16, A.PAYMENT_UPDATE_REQUEST: 0.68,
            A.PAYMENT_LINK: 0.54, A.CUSTOMER_NOTIFICATION: 0.47,
            A.ALTERNATE_PAYMENT_METHOD: 0.45, A.GRACE_PERIOD: 0.22,
        },
        FC.PERMANENT: {
            A.RETRY_SUBSCRIPTION: 0.01, A.DELAYED_RETRY: 0.01, A.PAYMENT_UPDATE_REQUEST: 0.05,
            A.PAYMENT_LINK: 0.04, A.CUSTOMER_NOTIFICATION: 0.03,
            A.ALTERNATE_PAYMENT_METHOD: 0.05, A.GRACE_PERIOD: 0.02,
        },
        FC.UNKNOWN: {
            A.RETRY_SUBSCRIPTION: 0.22, A.DELAYED_RETRY: 0.42, A.PAYMENT_UPDATE_REQUEST: 0.44,
            A.PAYMENT_LINK: 0.46, A.CUSTOMER_NOTIFICATION: 0.26,
            A.ALTERNATE_PAYMENT_METHOD: 0.41, A.GRACE_PERIOD: 0.30,
        },
    },
}

#: Why a cart was abandoned changes which nudge actually works.
ABANDONMENT_MODIFIERS: dict[str, dict[A, float]] = {
    "PRICE_CONCERN": {A.DISCOUNT_INCENTIVE: 1.55, A.REMINDER: 0.70, A.CHECKOUT_RESUME: 0.80},
    "PAYMENT_FRICTION": {
        A.ALTERNATE_PAYMENT_METHOD: 1.70, A.CHECKOUT_RESUME: 1.35, A.PAYMENT_LINK: 1.25,
        A.DISCOUNT_INCENTIVE: 0.70,
    },
    "DISTRACTION": {A.REMINDER: 1.75, A.CHECKOUT_RESUME: 1.40, A.DISCOUNT_INCENTIVE: 0.80},
    "COMPARISON_SHOPPING": {A.DISCOUNT_INCENTIVE: 1.45, A.REMINDER: 0.85},
    "UNEXPECTED_COST": {A.DISCOUNT_INCENTIVE: 1.60, A.PAYMENT_LINK: 0.85, A.REMINDER: 0.65},
    "TRUST_HESITATION": {
        A.CUSTOMER_NOTIFICATION: 1.50, A.DISCOUNT_INCENTIVE: 0.90, A.REMINDER: 1.10,
    },
}

#: Repeated attempts on the same opportunity get progressively less effective.
ATTEMPT_DECAY: tuple[float, ...] = (1.0, 0.80, 0.63, 0.48, 0.35)


@dataclass(slots=True)
class GroundTruth:
    """The simulator's hidden reality for one opportunity."""

    action_success_probs: dict[str, float]
    latent_factors: dict[str, float | str | bool]
    optimal_action: str
    optimal_probability: float
    is_recoverable: bool
    #: Filled in per attempt; not persisted.
    _cache: dict = field(default_factory=dict, repr=False)


def _attempt_decay(attempt_number: int) -> float:
    idx = max(0, attempt_number - 1)
    return ATTEMPT_DECAY[min(idx, len(ATTEMPT_DECAY) - 1)]


def _amount_modifier(action: A, amount_minor: int, price_sensitivity: float) -> float:
    """Bigger amounts are harder to re-collect; discounts work best on price-sensitive
    customers holding expensive carts."""
    rupees = amount_minor / 100.0
    size_penalty = clamp(1.0 - (rupees / 60_000.0) * 0.35, 0.62, 1.0)
    if action is A.DISCOUNT_INCENTIVE:
        return size_penalty * (0.75 + 0.85 * price_sensitivity)
    if action in (A.IMMEDIATE_RETRY, A.DELAYED_RETRY, A.RETRY_SUBSCRIPTION):
        return size_penalty
    return clamp(size_penalty + 0.08, 0.6, 1.0)


def _timing_modifier(action: A, hours_since_event: float) -> float:
    """A delayed retry needs time to pass; a cart nudge goes stale."""
    if action is A.DELAYED_RETRY:
        if hours_since_event < 0.5:
            return 0.70
        if hours_since_event > 72:
            return 0.85
        return 1.0
    if action is A.IMMEDIATE_RETRY:
        return 1.0 if hours_since_event < 1 else 0.80
    if action in (A.REMINDER, A.CHECKOUT_RESUME):
        return clamp(1.15 - (hours_since_event / 48.0) * 0.5, 0.45, 1.15)
    return 1.0


def compute_ground_truth(
    *,
    seed: int,
    opportunity_ref: str,
    scenario: Scenario,
    failure_category: FC,
    amount_minor: int,
    customer_reliability: float,
    price_sensitivity: float,
    unrecoverable_rate: float,
    abandonment_reason: str | None = None,
    hours_since_event: float = 1.0,
    attempt_number: int = 1,
) -> GroundTruth:
    """Compute per-action true success probabilities for one opportunity."""

    rng = derive_rng(seed, "ground_truth", opportunity_ref)

    table = BASE_PROBS.get(scenario, {})
    base = table.get(failure_category) or table.get(FC.UNKNOWN) or next(iter(table.values()), {})

    # Some opportunities are genuinely dead. Permanent-class failures are far more
    # likely to be, which is what makes STOP the correct answer some of the time.
    dead_bias = 3.0 if failure_category in (FC.PERMANENT, FC.EXPIRED_CARD) else 1.0
    is_recoverable = rng.random() >= clamp(unrecoverable_rate * dead_bias, 0.0, 0.95)

    # Per-opportunity noise: two customers with identical observable features still
    # differ, so no strategy can achieve a perfect score by memorising the table.
    noise = rng.gauss(1.0, 0.12)

    # Per-*action* noise matters more than it looks. With a single shared multiplier the
    # ranking of actions is fixed by the base table, so any strategy that memorises
    # "category -> best action" reaches the oracle and the benchmark proves nothing.
    # Independent noise per action means which remedy actually works varies between two
    # customers with the same failure code — as it does in reality — so the oracle
    # becomes a hindsight upper bound rather than an achievable target.
    action_noise = {
        str(a): derive_rng(seed, "gt_action", opportunity_ref, str(a)).gauss(1.0, 0.30)
        for a in base
    }
    # Tuned so that only genuinely strong (action, customer) pairs approach the ceiling:
    # if everything saturated, every action would look equally good and choosing well
    # would earn nothing.
    reliability_multiplier = 0.45 + 0.70 * customer_reliability

    probs: dict[str, float] = {}
    for action, base_p in base.items():
        if not is_recoverable:
            probs[str(action)] = 0.0
            continue
        p = base_p * reliability_multiplier * noise * max(0.15, action_noise[str(action)])
        p *= _attempt_decay(attempt_number)
        p *= _amount_modifier(action, amount_minor, price_sensitivity)
        p *= _timing_modifier(action, hours_since_event)
        if scenario is Scenario.CHECKOUT_ABANDONMENT and abandonment_reason:
            p *= ABANDONMENT_MODIFIERS.get(abandonment_reason, {}).get(action, 1.0)
        probs[str(action)] = round(clamp(p, 0.0, 0.95), 4)

    # STOP never recovers money — by definition, not by chance.
    probs[str(A.STOP)] = 0.0

    scored = {a: p for a, p in probs.items() if a != str(A.STOP)}
    if scored and max(scored.values()) > 0:
        optimal_action = max(scored, key=lambda a: scored[a])
        optimal_probability = scored[optimal_action]
    else:
        # Nothing works: stopping is genuinely correct.
        optimal_action, optimal_probability = str(A.STOP), 0.0

    return GroundTruth(
        action_success_probs=probs,
        latent_factors={
            "customer_reliability": round(customer_reliability, 4),
            "price_sensitivity": round(price_sensitivity, 4),
            "opportunity_noise": round(noise, 4),
            "abandonment_reason": abandonment_reason or "",
            "hours_since_event": round(hours_since_event, 3),
            "is_recoverable": is_recoverable,
        },
        optimal_action=optimal_action,
        optimal_probability=optimal_probability,
        is_recoverable=is_recoverable,
    )
