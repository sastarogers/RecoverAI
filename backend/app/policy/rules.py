"""Deterministic policy rules (§22).

Each rule is a pure function of (opportunity, context, decision, settings). No rule
consults the AI's probability or confidence as a reason to *permit* something — the AI
cannot argue its way past a guardrail (RULE 4). Confidence is only ever used to make a
rule stricter, never to relax one.

Every rule's outcome is recorded, so the opportunity detail page can show exactly which
guardrails were evaluated and which one stopped an action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.domain.context import RecoveryContext
from app.domain.enums import (
    NON_RETRYABLE_CATEGORIES,
    NOTIFYING_ACTIONS,
    RETRY_ACTIONS,
    SCENARIO_ACTIONS,
    FailureCategory,
    OpportunityStatus,
    RecoveryAction,
    RiskLevel,
    Scenario,
)


@dataclass(slots=True)
class PolicyInput:
    status: OpportunityStatus
    scenario: Scenario
    failure_category: FailureCategory
    attempt_count: int
    notification_count: int
    already_settled: bool
    minutes_since_last_attempt: float | None
    previous_actions: list[str]
    amount_at_risk_minor: int
    action: RecoveryAction
    confidence: float
    risk_level: RiskLevel


@dataclass(slots=True)
class PolicyLimits:
    max_attempts: int = 3
    max_notifications: int = 2
    cooldown_minutes: int = 0
    max_discount_minor: int = 100_000
    allow_high_risk_without_approval: bool = False
    min_confidence_for_high_risk: float = 0.5


@dataclass(slots=True)
class RuleResult:
    rule_id: str
    passed: bool
    message: str


Rule = Callable[[PolicyInput, PolicyLimits], RuleResult]


def _ok(rule_id: str, message: str) -> RuleResult:
    return RuleResult(rule_id, True, message)


def _fail(rule_id: str, message: str) -> RuleResult:
    return RuleResult(rule_id, False, message)


def p01_not_already_recovered(i: PolicyInput, _l: PolicyLimits) -> RuleResult:
    """Never act on revenue that has already been recovered (RULE 3)."""
    if i.status == OpportunityStatus.RECOVERED or i.already_settled:
        return _fail("P01_ALREADY_RECOVERED", "Opportunity has already been recovered")
    return _ok("P01_ALREADY_RECOVERED", "Not yet recovered")


def p02_not_terminal(i: PolicyInput, _l: PolicyLimits) -> RuleResult:
    if i.status in (OpportunityStatus.EXHAUSTED, OpportunityStatus.EXPIRED):
        return _fail("P02_TERMINAL", f"Opportunity is closed ({i.status})")
    return _ok("P02_TERMINAL", "Opportunity is open")


def p03_within_attempt_limit(i: PolicyInput, limits: PolicyLimits) -> RuleResult:
    if i.attempt_count >= limits.max_attempts:
        return _fail(
            "P03_MAX_ATTEMPTS",
            f"Attempt limit reached ({i.attempt_count}/{limits.max_attempts})",
        )
    return _ok(
        "P03_MAX_ATTEMPTS", f"Attempt {i.attempt_count + 1} of {limits.max_attempts}"
    )


def p04_no_retry_on_non_retryable(i: PolicyInput, _l: PolicyLimits) -> RuleResult:
    """A blocked card or a wrong VPA will not start working because we asked again."""
    if i.action in RETRY_ACTIONS and i.failure_category in NON_RETRYABLE_CATEGORIES:
        return _fail(
            "P04_NON_RETRYABLE_FAILURE",
            f"{i.action} is not permitted for a {i.failure_category} failure",
        )
    return _ok("P04_NON_RETRYABLE_FAILURE", "Retry is appropriate for this failure class")


def p05_no_repeated_failed_retry(i: PolicyInput, _l: PolicyLimits) -> RuleResult:
    """Do not run the identical retry that already failed on this opportunity."""
    if i.action is RecoveryAction.IMMEDIATE_RETRY and str(i.action) in i.previous_actions:
        return _fail(
            "P05_REPEATED_RETRY", "An immediate retry has already been attempted and failed"
        )
    return _ok("P05_REPEATED_RETRY", "Action has not already failed")


def p06_notification_fatigue(i: PolicyInput, limits: PolicyLimits) -> RuleResult:
    if i.action in NOTIFYING_ACTIONS and i.notification_count >= limits.max_notifications:
        return _fail(
            "P06_NOTIFICATION_FATIGUE",
            f"Customer contact limit reached ({i.notification_count}/"
            f"{limits.max_notifications})",
        )
    return _ok("P06_NOTIFICATION_FATIGUE", "Within customer contact limits")


def p07_action_matches_scenario(i: PolicyInput, _l: PolicyLimits) -> RuleResult:
    """Independent re-check of what the prompt already constrained."""
    if i.action not in set(SCENARIO_ACTIONS[i.scenario]):
        return _fail(
            "P07_ACTION_SCENARIO_MISMATCH", f"{i.action} is not valid for {i.scenario}"
        )
    return _ok("P07_ACTION_SCENARIO_MISMATCH", "Action is valid for this scenario")


def p08_discount_within_limit(i: PolicyInput, limits: PolicyLimits) -> RuleResult:
    if (
        i.action is RecoveryAction.DISCOUNT_INCENTIVE
        and i.amount_at_risk_minor > limits.max_discount_minor
        and not limits.allow_high_risk_without_approval
    ):
        return _fail(
            "P08_DISCOUNT_REQUIRES_APPROVAL",
            "Discount on a high-value cart requires manual approval",
        )
    return _ok("P08_DISCOUNT_REQUIRES_APPROVAL", "Discount within automatic limits")


def p09_cooldown_elapsed(i: PolicyInput, limits: PolicyLimits) -> RuleResult:
    if (
        limits.cooldown_minutes > 0
        and i.minutes_since_last_attempt is not None
        and i.minutes_since_last_attempt < limits.cooldown_minutes
    ):
        return _fail(
            "P09_COOLDOWN",
            f"Cooldown active ({i.minutes_since_last_attempt:.0f} of "
            f"{limits.cooldown_minutes} minutes elapsed)",
        )
    return _ok("P09_COOLDOWN", "Cooldown satisfied")


def p12_high_risk_needs_confidence(i: PolicyInput, limits: PolicyLimits) -> RuleResult:
    """A high-risk action taken on a low-confidence recommendation is not worth it.

    Note the asymmetry: high confidence never *unlocks* anything — it only fails to
    trigger this rule.
    """
    if i.risk_level is RiskLevel.HIGH and i.confidence < limits.min_confidence_for_high_risk:
        return _fail(
            "P12_LOW_CONFIDENCE_HIGH_RISK",
            f"High-risk action at {i.confidence:.0%} confidence requires approval",
        )
    return _ok("P12_LOW_CONFIDENCE_HIGH_RISK", "Risk and confidence are acceptable")


#: Evaluated in order; all are evaluated so the audit trail is complete.
ALL_RULES: tuple[Rule, ...] = (
    p01_not_already_recovered,
    p02_not_terminal,
    p03_within_attempt_limit,
    p04_no_retry_on_non_retryable,
    p05_no_repeated_failed_retry,
    p06_notification_fatigue,
    p07_action_matches_scenario,
    p08_discount_within_limit,
    p09_cooldown_elapsed,
    p12_high_risk_needs_confidence,
)


def context_to_policy_input(
    context: RecoveryContext,
    *,
    status: OpportunityStatus,
    action: RecoveryAction,
    confidence: float,
    risk_level: RiskLevel,
    amount_at_risk_minor: int,
    already_settled: bool,
    minutes_since_last_attempt: float | None,
) -> PolicyInput:
    return PolicyInput(
        status=status,
        scenario=context.scenario,
        failure_category=FailureCategory(context.failure.category),
        attempt_count=context.recovery_history.attempt_count,
        notification_count=context.recovery_history.notification_count,
        already_settled=already_settled,
        minutes_since_last_attempt=minutes_since_last_attempt,
        previous_actions=list(context.recovery_history.previous_actions),
        amount_at_risk_minor=amount_at_risk_minor,
        action=action,
        confidence=confidence,
        risk_level=risk_level,
    )
