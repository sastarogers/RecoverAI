"""The policy engine — the deterministic gate between AI and money (§21/§22).

Architecture guarantee: `AI -> Policy -> Executor`. There is no path from a
recommendation to an execution that does not pass through `evaluate()`, and the state
machine independently refuses any transition into EXECUTING that did not come from
APPROVED.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.enums import PolicyVerdict, RecoveryAction
from app.policy.rules import ALL_RULES, PolicyInput, PolicyLimits, RuleResult

log = get_logger("recoverai.policy")


@dataclass(slots=True)
class PolicyOutcome:
    verdict: PolicyVerdict
    requested_action: RecoveryAction
    effective_action: RecoveryAction | None
    reason: str
    blocked_by_rule: str | None = None
    rules_evaluated: list[dict] = field(default_factory=list)
    #: True when the verdict is "stop deliberately", not "blocked by a guardrail".
    is_stop: bool = False

    @property
    def approved(self) -> bool:
        return self.verdict is PolicyVerdict.APPROVED

    @property
    def should_execute(self) -> bool:
        return self.approved and not self.is_stop


def limits_from_settings(
    *, max_attempts: int | None = None, max_notifications: int | None = None
) -> PolicyLimits:
    return PolicyLimits(
        max_attempts=max_attempts or settings.policy_max_attempts,
        max_notifications=(
            max_notifications
            if max_notifications is not None
            else settings.policy_max_notifications
        ),
        cooldown_minutes=settings.policy_cooldown_minutes,
        max_discount_minor=settings.policy_max_discount_minor,
    )


def evaluate(policy_input: PolicyInput, limits: PolicyLimits | None = None) -> PolicyOutcome:
    """Run every rule, record every result, and return the verdict."""
    limits = limits or limits_from_settings()

    results: list[RuleResult] = [rule(policy_input, limits) for rule in ALL_RULES]
    trace = [
        {"rule_id": r.rule_id, "passed": r.passed, "message": r.message} for r in results
    ]

    # STOP is a legitimate recommendation, not an action to guard. It is still checked
    # against the "already recovered" and "terminal" rules so it cannot reopen closed work.
    if policy_input.action is RecoveryAction.STOP:
        blocking = next(
            (r for r in results if not r.passed and r.rule_id.startswith(("P01", "P02"))),
            None,
        )
        if blocking is not None:
            return PolicyOutcome(
                verdict=PolicyVerdict.BLOCKED,
                requested_action=RecoveryAction.STOP,
                effective_action=None,
                reason=blocking.message,
                blocked_by_rule=blocking.rule_id,
                rules_evaluated=trace,
            )
        return PolicyOutcome(
            verdict=PolicyVerdict.APPROVED,
            requested_action=RecoveryAction.STOP,
            effective_action=RecoveryAction.STOP,
            reason="Stopping recovery is permitted and closes the opportunity",
            rules_evaluated=trace,
            is_stop=True,
        )

    failed = [r for r in results if not r.passed]
    if failed:
        first = failed[0]
        log.info(
            "policy.blocked",
            rule=first.rule_id,
            action=str(policy_input.action),
            scenario=str(policy_input.scenario),
        )
        return PolicyOutcome(
            verdict=PolicyVerdict.BLOCKED,
            requested_action=policy_input.action,
            effective_action=None,
            reason=first.message,
            blocked_by_rule=first.rule_id,
            rules_evaluated=trace,
        )

    return PolicyOutcome(
        verdict=PolicyVerdict.APPROVED,
        requested_action=policy_input.action,
        effective_action=policy_input.action,
        reason=f"{policy_input.action} passed all {len(results)} policy checks",
        rules_evaluated=trace,
    )
