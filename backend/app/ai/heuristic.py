"""Deterministic recovery strategist.

Two jobs:
  1. The **fallback** whenever the LLM is unavailable, times out, or returns something
     that fails validation — the platform must never stall because the AI did (§48).
  2. A first-class **decision mode** (`ai_mode=heuristic`) that makes an entire
     simulation run bit-for-bit reproducible, which is what the reproducibility
     guarantee in §57 depends on.

It reads exactly the same observable context the LLM gets. It has no access to ground
truth — it competes on inference, like the model does.
"""

from __future__ import annotations

from app.ai.schema import AIDecisionOutput
from app.core.rng import clamp
from app.domain.context import RecoveryContext
from app.domain.enums import (
    DELAY_ONLY_CATEGORIES,
    NON_RETRYABLE_CATEGORIES,
    SCENARIO_ACTIONS,
    FailureCategory,
    RecoveryAction,
    RiskLevel,
    Scenario,
)

#: Prior belief about how well each action works, before context is applied.
#: Deliberately *not* the simulator's table — this engine must earn its accuracy.
_ACTION_PRIOR: dict[FailureCategory, dict[RecoveryAction, float]] = {
    FailureCategory.TEMPORARY: {
        RecoveryAction.DELAYED_RETRY: 0.78, RecoveryAction.IMMEDIATE_RETRY: 0.44,
        RecoveryAction.PAYMENT_LINK: 0.58, RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.50,
        RecoveryAction.RETRY_SUBSCRIPTION: 0.52, RecoveryAction.CUSTOMER_NOTIFICATION: 0.28,
    },
    FailureCategory.NETWORK_ERROR: {
        RecoveryAction.DELAYED_RETRY: 0.72, RecoveryAction.IMMEDIATE_RETRY: 0.60,
        RecoveryAction.PAYMENT_LINK: 0.54, RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.48,
        RecoveryAction.RETRY_SUBSCRIPTION: 0.58, RecoveryAction.CUSTOMER_NOTIFICATION: 0.24,
    },
    FailureCategory.TIMEOUT: {
        RecoveryAction.DELAYED_RETRY: 0.74, RecoveryAction.IMMEDIATE_RETRY: 0.48,
        RecoveryAction.PAYMENT_LINK: 0.55, RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.49,
        RecoveryAction.RETRY_SUBSCRIPTION: 0.50, RecoveryAction.CUSTOMER_NOTIFICATION: 0.26,
    },
    FailureCategory.BANK_DECLINE: {
        RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.58, RecoveryAction.PAYMENT_LINK: 0.43,
        RecoveryAction.DELAYED_RETRY: 0.32, RecoveryAction.IMMEDIATE_RETRY: 0.11,
        RecoveryAction.PAYMENT_UPDATE_REQUEST: 0.54, RecoveryAction.RETRY_SUBSCRIPTION: 0.12,
        RecoveryAction.CUSTOMER_NOTIFICATION: 0.23, RecoveryAction.GRACE_PERIOD: 0.29,
    },
    FailureCategory.INSUFFICIENT_FUNDS: {
        RecoveryAction.DELAYED_RETRY: 0.45, RecoveryAction.PAYMENT_LINK: 0.52,
        RecoveryAction.CUSTOMER_NOTIFICATION: 0.39, RecoveryAction.GRACE_PERIOD: 0.53,
        RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.35, RecoveryAction.IMMEDIATE_RETRY: 0.07,
        RecoveryAction.RETRY_SUBSCRIPTION: 0.09, RecoveryAction.PAYMENT_UPDATE_REQUEST: 0.41,
    },
    FailureCategory.INVALID_PAYMENT_DETAILS: {
        RecoveryAction.PAYMENT_UPDATE_REQUEST: 0.68, RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.51,
        RecoveryAction.PAYMENT_LINK: 0.47, RecoveryAction.CUSTOMER_NOTIFICATION: 0.33,
    },
    FailureCategory.EXPIRED_CARD: {
        RecoveryAction.PAYMENT_UPDATE_REQUEST: 0.72, RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.56,
        RecoveryAction.PAYMENT_LINK: 0.49, RecoveryAction.CUSTOMER_NOTIFICATION: 0.37,
    },
    FailureCategory.CUSTOMER_ACTION_REQUIRED: {
        RecoveryAction.PAYMENT_UPDATE_REQUEST: 0.66, RecoveryAction.PAYMENT_LINK: 0.60,
        RecoveryAction.CUSTOMER_NOTIFICATION: 0.46, RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.43,
    },
    FailureCategory.PERMANENT: {},
    FailureCategory.UNKNOWN: {
        RecoveryAction.DELAYED_RETRY: 0.40, RecoveryAction.PAYMENT_LINK: 0.44,
        RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.40, RecoveryAction.IMMEDIATE_RETRY: 0.19,
        RecoveryAction.RETRY_SUBSCRIPTION: 0.21, RecoveryAction.CUSTOMER_NOTIFICATION: 0.24,
        RecoveryAction.GRACE_PERIOD: 0.28, RecoveryAction.PAYMENT_UPDATE_REQUEST: 0.42,
    },
    FailureCategory.ABANDONED: {
        RecoveryAction.DISCOUNT_INCENTIVE: 0.50, RecoveryAction.CHECKOUT_RESUME: 0.42,
        RecoveryAction.PAYMENT_LINK: 0.38, RecoveryAction.REMINDER: 0.25,
        RecoveryAction.ALTERNATE_PAYMENT_METHOD: 0.29, RecoveryAction.CUSTOMER_NOTIFICATION: 0.18,
    },
}

#: Cart-abandonment reasons point at different remedies.
_REASON_BIAS: dict[str, dict[RecoveryAction, float]] = {
    "PRICE_CONCERN": {RecoveryAction.DISCOUNT_INCENTIVE: 1.5, RecoveryAction.REMINDER: 0.7},
    "PAYMENT_FRICTION": {
        RecoveryAction.ALTERNATE_PAYMENT_METHOD: 1.7, RecoveryAction.CHECKOUT_RESUME: 1.3,
        RecoveryAction.DISCOUNT_INCENTIVE: 0.7,
    },
    "DISTRACTION": {RecoveryAction.REMINDER: 1.7, RecoveryAction.CHECKOUT_RESUME: 1.35},
    "COMPARISON_SHOPPING": {RecoveryAction.DISCOUNT_INCENTIVE: 1.4},
    "UNEXPECTED_COST": {RecoveryAction.DISCOUNT_INCENTIVE: 1.55, RecoveryAction.REMINDER: 0.7},
    "TRUST_HESITATION": {RecoveryAction.CUSTOMER_NOTIFICATION: 1.45},
}

_ATTEMPT_DECAY = (1.0, 0.80, 0.63, 0.48, 0.35)

#: Calibration factor applied to the raw score before reporting it as a probability.
#:
#: The scoring function above ranks actions well but is not a probability: it does not
#: know that a share of failures are structurally unrecoverable whatever you do, nor how
#: much per-customer variance sits underneath. Reporting the raw score would overstate
#: recovery odds by roughly 2x, and that number is shown to merchants and used in the
#: expected-value column.
#:
#: This is a shrink toward the observed historical recovery base rate — a merchant-side
#: statistic, not simulator ground truth. It scales every action identically, so the
#: chosen action never changes; only the honesty of the reported number does.
RECOVERY_BASE_RATE_PRIOR = 0.55


def _decay(attempt_index: int) -> float:
    return _ATTEMPT_DECAY[min(max(attempt_index, 0), len(_ATTEMPT_DECAY) - 1)]


def decide(context: RecoveryContext) -> AIDecisionOutput:
    """Score every allowed action against observable context and pick the best."""
    category = FailureCategory(context.failure.category)
    allowed = [
        a for a in context.allowed_actions if a in set(SCENARIO_ACTIONS[context.scenario])
    ]
    attempt_index = context.recovery_history.attempt_count

    # A non-retryable failure will not be fixed by trying the same charge again.
    if category in NON_RETRYABLE_CATEGORIES:
        allowed = [
            a
            for a in allowed
            if a
            not in (
                RecoveryAction.IMMEDIATE_RETRY,
                RecoveryAction.DELAYED_RETRY,
                RecoveryAction.RETRY_SUBSCRIPTION,
            )
        ]
    if category in DELAY_ONLY_CATEGORIES:
        allowed = [a for a in allowed if a is not RecoveryAction.IMMEDIATE_RETRY]

    priors = _ACTION_PRIOR.get(category, {})
    reliability = clamp(context.customer.historical_success_rate or 0.5)
    scores: dict[RecoveryAction, float] = {}

    for action in allowed:
        if action is RecoveryAction.STOP:
            continue
        base = priors.get(action)
        if base is None:
            continue
        score = base

        # Customers with a strong history convert better on every channel.
        score *= 0.70 + 0.55 * reliability
        score *= _decay(attempt_index)

        # Do not repeat something that already failed on this opportunity.
        if str(action) in context.recovery_history.previous_actions:
            score *= 0.35

        if action is RecoveryAction.DELAYED_RETRY:
            hours = context.recovery_history.hours_since_detection
            score *= 0.75 if hours < 0.5 else 1.0
        if action is RecoveryAction.IMMEDIATE_RETRY:
            score *= 1.0 if context.recovery_history.hours_since_detection < 1 else 0.8

        if context.checkout is not None:
            bias = _REASON_BIAS.get(context.checkout.abandonment_reason or "", {})
            score *= bias.get(action, 1.0)
            # Stale carts respond poorly to a plain nudge.
            if action in (RecoveryAction.REMINDER, RecoveryAction.CHECKOUT_RESUME):
                mins = context.checkout.minutes_since_abandonment
                score *= clamp(1.15 - (mins / 2880.0), 0.4, 1.15)
            if action is RecoveryAction.DISCOUNT_INCENTIVE:
                # Discounting a customer who reliably converts anyway gives away margin.
                score *= 0.75 if context.checkout.has_previously_converted else 1.1
            if not context.checkout.has_previously_converted:
                score *= 0.85

        if context.subscription is not None and action is RecoveryAction.PAYMENT_UPDATE_REQUEST:
            # Long-tenured subscribers are worth asking to fix their payment method.
            score *= 1.0 + 0.25 * clamp(context.subscription.subscription_age_days / 365)

        # Large amounts are harder to re-collect.
        score *= clamp(1.0 - (context.amount_at_risk / 60_000.0) * 0.3, 0.65, 1.0)

        scores[action] = clamp(score, 0.0, 0.95)

    notifications_exhausted = (
        context.recovery_history.notification_count >= 2
    )
    if notifications_exhausted:
        scores = {a: s for a, s in scores.items() if a is not RecoveryAction.CUSTOMER_NOTIFICATION}

    if not scores or max(scores.values()) < 0.12:
        return AIDecisionOutput(
            action=RecoveryAction.STOP,
            recovery_probability=0.0,
            confidence=0.80,
            reason=_stop_reason(category, attempt_index),
            risk_level=RiskLevel.LOW,
        )

    best = max(scores, key=lambda a: scores[a])
    probability = round(clamp(scores[best] * RECOVERY_BASE_RATE_PRIOR, 0.0, 0.95), 4)

    ranked = sorted(scores.values(), reverse=True)
    margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0)
    confidence = round(clamp(0.55 + margin * 1.4 + 0.1 * reliability, 0.3, 0.97), 4)

    return AIDecisionOutput(
        action=best,
        recovery_probability=probability,
        confidence=confidence,
        reason=_explain(best, context, category, probability),
        risk_level=_risk(context, best, probability),
    )


def _stop_reason(category: FailureCategory, attempt_index: int) -> str:
    if category in NON_RETRYABLE_CATEGORIES:
        return (
            f"{category} cannot be resolved by another attempt on the same instrument; "
            "further outreach would cost more than it can recover."
        )
    if attempt_index >= 2:
        return (
            f"{attempt_index} recovery attempts have already failed; remaining actions "
            "have too low an expected return to justify contacting the customer again."
        )
    return "No available action has a materially positive chance of recovering this revenue."


def _explain(
    action: RecoveryAction, ctx: RecoveryContext, category: FailureCategory, p: float
) -> str:
    rate = int(round((ctx.customer.historical_success_rate or 0) * 100))
    attempt = ctx.recovery_history.attempt_count + 1
    if ctx.scenario is Scenario.CHECKOUT_ABANDONMENT and ctx.checkout is not None:
        reason = (ctx.checkout.abandonment_reason or "unclear reason").replace("_", " ").lower()
        converted = "a previously converting" if ctx.checkout.has_previously_converted else "a new"
        return (
            f"{action} suits {converted} customer who abandoned a "
            f"{ctx.currency} {ctx.amount_at_risk:,.0f} cart over {reason}, "
            f"{ctx.checkout.minutes_since_abandonment:.0f} minutes ago (attempt {attempt})."
        )
    if ctx.scenario is Scenario.FAILED_SUBSCRIPTION and ctx.subscription is not None:
        return (
            f"{category} on a renewal with {ctx.subscription.previous_successful_renewals} "
            f"prior successful cycles; {action} is the highest-yield option at "
            f"attempt {attempt} (est. {p:.0%})."
        )
    return (
        f"{category} failure with a {rate}% historical success rate for this customer "
        f"makes {action} the strongest option on attempt {attempt} (est. {p:.0%})."
    )


def _risk(ctx: RecoveryContext, action: RecoveryAction, probability: float) -> RiskLevel:
    if action is RecoveryAction.DISCOUNT_INCENTIVE:
        return RiskLevel.HIGH if ctx.amount_at_risk > 5000 else RiskLevel.MEDIUM
    if probability < 0.30 or ctx.recovery_history.attempt_count >= 2:
        return RiskLevel.MEDIUM
    if action in (RecoveryAction.CUSTOMER_NOTIFICATION, RecoveryAction.REMINDER):
        return RiskLevel.MEDIUM if ctx.recovery_history.notification_count else RiskLevel.LOW
    return RiskLevel.LOW
