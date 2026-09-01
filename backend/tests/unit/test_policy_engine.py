"""§22 — the guardrails. RULE 4: the AI cannot bypass any of these."""

import pytest

from app.domain.enums import (
    FailureCategory,
    OpportunityStatus,
    PolicyVerdict,
    RecoveryAction,
    RiskLevel,
    Scenario,
)
from app.policy.engine import evaluate
from app.policy.rules import PolicyInput, PolicyLimits

LIMITS = PolicyLimits(max_attempts=3, max_notifications=2, max_discount_minor=100_000)


def _input(**overrides) -> PolicyInput:
    base = dict(
        status=OpportunityStatus.RECOMMENDED,
        scenario=Scenario.FAILED_PAYMENT,
        failure_category=FailureCategory.TEMPORARY,
        attempt_count=0,
        notification_count=0,
        already_settled=False,
        minutes_since_last_attempt=None,
        previous_actions=[],
        amount_at_risk_minor=500_000,
        action=RecoveryAction.DELAYED_RETRY,
        confidence=0.9,
        risk_level=RiskLevel.LOW,
    )
    base.update(overrides)
    return PolicyInput(**base)


def test_reasonable_action_is_approved():
    outcome = evaluate(_input(), LIMITS)
    assert outcome.verdict is PolicyVerdict.APPROVED
    assert outcome.effective_action is RecoveryAction.DELAYED_RETRY
    assert outcome.should_execute


def test_retry_limit_blocks_further_attempts():
    """§22 verbatim: AI recommends IMMEDIATE_RETRY at retry_count = 3 -> BLOCK."""
    outcome = evaluate(_input(action=RecoveryAction.IMMEDIATE_RETRY, attempt_count=3), LIMITS)
    assert outcome.verdict is PolicyVerdict.BLOCKED
    assert outcome.blocked_by_rule == "P03_MAX_ATTEMPTS"


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.PERMANENT,
        FailureCategory.EXPIRED_CARD,
        FailureCategory.INVALID_PAYMENT_DETAILS,
    ],
)
def test_permanent_failures_are_never_retried(category):
    """§22 verbatim: DELAYED_RETRY on a PERMANENT failure -> BLOCK (RULE 5)."""
    outcome = evaluate(
        _input(action=RecoveryAction.DELAYED_RETRY, failure_category=category), LIMITS
    )
    assert outcome.verdict is PolicyVerdict.BLOCKED
    assert outcome.blocked_by_rule == "P04_NON_RETRYABLE_FAILURE"


def test_changing_the_instrument_is_still_allowed_on_a_dead_card():
    """The card is dead, but asking the customer for a new one is not a retry."""
    outcome = evaluate(
        _input(
            action=RecoveryAction.PAYMENT_LINK, failure_category=FailureCategory.EXPIRED_CARD
        ),
        LIMITS,
    )
    assert outcome.verdict is PolicyVerdict.APPROVED


def test_already_recovered_opportunity_blocks_everything():
    """RULE 3 at the policy layer: no second bite at settled revenue."""
    outcome = evaluate(_input(status=OpportunityStatus.RECOVERED), LIMITS)
    assert outcome.verdict is PolicyVerdict.BLOCKED
    assert outcome.blocked_by_rule == "P01_ALREADY_RECOVERED"


def test_settled_ledger_entry_blocks_even_if_status_lags():
    outcome = evaluate(_input(already_settled=True), LIMITS)
    assert outcome.blocked_by_rule == "P01_ALREADY_RECOVERED"


def test_notification_fatigue_is_enforced():
    outcome = evaluate(
        _input(action=RecoveryAction.CUSTOMER_NOTIFICATION, notification_count=2), LIMITS
    )
    assert outcome.verdict is PolicyVerdict.BLOCKED
    assert outcome.blocked_by_rule == "P06_NOTIFICATION_FATIGUE"


def test_cross_scenario_action_is_blocked():
    outcome = evaluate(
        _input(scenario=Scenario.CHECKOUT_ABANDONMENT, action=RecoveryAction.RETRY_SUBSCRIPTION),
        LIMITS,
    )
    assert outcome.verdict is PolicyVerdict.BLOCKED
    assert outcome.blocked_by_rule == "P07_ACTION_SCENARIO_MISMATCH"


def test_large_discount_requires_approval():
    outcome = evaluate(
        _input(
            scenario=Scenario.CHECKOUT_ABANDONMENT,
            action=RecoveryAction.DISCOUNT_INCENTIVE,
            amount_at_risk_minor=900_000,
            risk_level=RiskLevel.MEDIUM,
        ),
        LIMITS,
    )
    assert outcome.blocked_by_rule == "P08_DISCOUNT_REQUIRES_APPROVAL"


def test_repeated_immediate_retry_is_blocked():
    outcome = evaluate(
        _input(
            action=RecoveryAction.IMMEDIATE_RETRY,
            attempt_count=1,
            previous_actions=["IMMEDIATE_RETRY"],
        ),
        LIMITS,
    )
    assert outcome.blocked_by_rule == "P05_REPEATED_RETRY"


def test_cooldown_blocks_a_too_soon_attempt():
    outcome = evaluate(
        _input(attempt_count=1, minutes_since_last_attempt=2.0),
        PolicyLimits(max_attempts=3, cooldown_minutes=30),
    )
    assert outcome.blocked_by_rule == "P09_COOLDOWN"


def test_high_confidence_cannot_unlock_a_blocked_action():
    """RULE 4: certainty is not authority. Confidence never relaxes a guardrail."""
    outcome = evaluate(
        _input(
            action=RecoveryAction.IMMEDIATE_RETRY,
            failure_category=FailureCategory.PERMANENT,
            confidence=1.0,
            risk_level=RiskLevel.LOW,
        ),
        LIMITS,
    )
    assert outcome.verdict is PolicyVerdict.BLOCKED


def test_low_confidence_high_risk_is_blocked():
    outcome = evaluate(_input(risk_level=RiskLevel.HIGH, confidence=0.2), LIMITS)
    assert outcome.blocked_by_rule == "P12_LOW_CONFIDENCE_HIGH_RISK"


def test_stop_is_approved_but_does_not_execute():
    outcome = evaluate(_input(action=RecoveryAction.STOP, attempt_count=3), LIMITS)
    assert outcome.verdict is PolicyVerdict.APPROVED
    assert outcome.is_stop is True
    assert outcome.should_execute is False


def test_stop_cannot_reopen_a_recovered_opportunity():
    outcome = evaluate(
        _input(action=RecoveryAction.STOP, status=OpportunityStatus.RECOVERED), LIMITS
    )
    assert outcome.verdict is PolicyVerdict.BLOCKED


def test_every_rule_is_recorded_for_the_audit_trail():
    outcome = evaluate(_input(), LIMITS)
    assert len(outcome.rules_evaluated) == 10
    assert all({"rule_id", "passed", "message"} <= set(r) for r in outcome.rules_evaluated)


def test_blocked_outcome_still_records_all_rules():
    outcome = evaluate(_input(attempt_count=5), LIMITS)
    assert outcome.verdict is PolicyVerdict.BLOCKED
    assert len(outcome.rules_evaluated) == 10
