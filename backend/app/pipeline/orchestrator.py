"""The unified recovery pipeline (§3, §60).

    context -> AI decision -> policy -> execution -> outcome -> ledger

One cycle = one recovery attempt. Razorpay events and simulator events both arrive
here, and neither gets a special path (RULE 8).

The ordering below is the whole design. AI output is persisted *before* policy runs;
policy is persisted *before* execution; ground truth is touched only inside execution;
and the ledger is the only thing that writes money.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import RecoveryAgent
from app.context.builder import _offer_actions, build_context
from app.core.ids import utcnow
from app.core.logging import get_logger
from app.db.models import (
    AIDecision,
    PolicyDecision,
    RecoveryAttempt,
    RecoveryLedger,
    RecoveryOpportunity,
    RecoveryOutcome,
)
from app.domain.enums import (
    NOTIFYING_ACTIONS,
    Actor,
    ExecutionStatus,
    LedgerEntryType,
    OpportunityStatus,
    Outcome,
    PolicyVerdict,
    RecoveryAction,
)
from app.domain.state_machine import transition
from app.executor.base import ExecutionResult, NoopExecutor, RecoveryExecutor
from app.ledger.settlement import settle_recovery
from app.policy import engine as policy_engine
from app.policy.rules import PolicyLimits, context_to_policy_input
from app.services import audit, refs

log = get_logger("recoverai.pipeline")


@dataclass(slots=True)
class CycleState:
    """Loop-carried state, so a multi-attempt opportunity does not re-query per cycle."""

    refused: set[str] = field(default_factory=set)
    decision_sequence: int = 0


@dataclass(slots=True)
class CycleResult:
    opportunity_ref: str
    ran: bool
    action: str | None
    decision_source: str | None
    policy_verdict: str | None
    blocked_by_rule: str | None
    outcome: str | None
    recovered_amount_minor: int
    settled: bool
    status: str
    reason: str


async def run_cycle(
    session: AsyncSession,
    opportunity: RecoveryOpportunity,
    *,
    agent: RecoveryAgent,
    executor: RecoveryExecutor,
    limits: PolicyLimits | None = None,
    state: CycleState | None = None,
    customer=None,
) -> CycleResult:
    """Advance one opportunity by exactly one recovery attempt.

    `state` lets a caller that is already looping over this opportunity carry the
    decision count and the set of refused actions forward instead of re-deriving them
    from the database on every cycle. Called standalone (the manual-recover endpoint),
    it is derived fresh.
    """

    limits = limits or policy_engine.limits_from_settings()

    if OpportunityStatus(opportunity.status) in (
        OpportunityStatus.RECOVERED,
        OpportunityStatus.EXHAUSTED,
        OpportunityStatus.EXPIRED,
    ):
        return _skip(opportunity, "opportunity is closed")

    # ---- 1. CONTEXT (observable only) --------------------------------
    transition(opportunity, OpportunityStatus.ANALYZING)
    if state is None:
        state = CycleState(
            refused=await _refused_actions(session, opportunity),
            decision_sequence=await _next_decision_sequence(session, opportunity) - 1,
        )
    refused = state.refused
    context = await build_context(
        session, opportunity, excluded_actions=refused, customer=customer
    )
    opportunity.context_snapshot = context.to_prompt_dict()

    # ---- 2. AI DECISION ----------------------------------------------
    decision = await agent.decide(context)
    attempt_number = opportunity.attempt_count + 1
    state.decision_sequence += 1
    sequence = state.decision_sequence

    ai_row = AIDecision(
        opportunity_id=opportunity.id,
        sequence=sequence,
        attempt_number=attempt_number,
        action=decision.output.action,
        recovery_probability=decision.output.recovery_probability,
        confidence=decision.output.confidence,
        reason=decision.output.reason,
        risk_level=decision.output.risk_level,
        decision_source=decision.source,
        model=decision.model,
        latency_ms=decision.latency_ms,
        context_signature=decision.context_signature,
        raw_response=decision.raw_response or {},
        validation_errors=decision.validation_errors,
    )
    session.add(ai_row)
    await session.flush()
    transition(opportunity, OpportunityStatus.RECOMMENDED)

    await audit.record(
        session,
        entity_type="recovery_opportunity",
        entity_id=opportunity.opportunity_ref,
        actor=Actor.AI,
        action="AI_RECOMMENDED",
        detail={
            "action": str(decision.output.action),
            "recovery_probability": decision.output.recovery_probability,
            "confidence": decision.output.confidence,
            "decision_source": str(decision.source),
            "attempt_number": attempt_number,
        },
        simulation_run_id=opportunity.simulation_run_id,
    )

    # ---- 3. POLICY (deterministic gate) ------------------------------
    already_settled = await _has_ledger_entry(session, opportunity)
    policy_input = context_to_policy_input(
        context,
        status=OpportunityStatus(opportunity.status),
        action=decision.output.action,
        confidence=decision.output.confidence,
        risk_level=decision.output.risk_level,
        amount_at_risk_minor=int(opportunity.amount_at_risk_minor),
        already_settled=already_settled,
        minutes_since_last_attempt=await _minutes_since_last_attempt(session, opportunity),
    )
    verdict = policy_engine.evaluate(policy_input, limits)

    policy_row = PolicyDecision(
        opportunity_id=opportunity.id,
        ai_decision_id=ai_row.id,
        verdict=verdict.verdict,
        requested_action=verdict.requested_action,
        effective_action=verdict.effective_action,
        blocked_by_rule=verdict.blocked_by_rule,
        reason=verdict.reason,
        rules_evaluated=verdict.rules_evaluated,
    )
    session.add(policy_row)
    await session.flush()

    await audit.record(
        session,
        entity_type="recovery_opportunity",
        entity_id=opportunity.opportunity_ref,
        actor=Actor.POLICY,
        action=f"POLICY_{verdict.verdict}",
        detail={
            "requested_action": str(verdict.requested_action),
            "blocked_by_rule": verdict.blocked_by_rule,
            "reason": verdict.reason,
        },
        simulation_run_id=opportunity.simulation_run_id,
    )

    # ---- 3a. Blocked, or a deliberate stop ---------------------------
    if verdict.verdict is PolicyVerdict.BLOCKED:
        transition(opportunity, OpportunityStatus.BLOCKED)
        state.refused.add(str(verdict.requested_action))
        refused_now = state.refused
        no_options_left = len(
            _offer_actions(context.scenario, refused_now)
        ) <= 1  # only STOP survives
        if (
            opportunity.attempt_count >= limits.max_attempts
            or _is_hard_block(verdict)
            or no_options_left
        ):
            _close(opportunity, OpportunityStatus.EXHAUSTED)
        return CycleResult(
            opportunity.opportunity_ref, False, str(verdict.requested_action),
            str(decision.source), str(verdict.verdict), verdict.blocked_by_rule,
            None, 0, False, str(opportunity.status), verdict.reason,
        )

    transition(opportunity, OpportunityStatus.APPROVED)

    if verdict.is_stop:
        _close(opportunity, OpportunityStatus.EXHAUSTED)
        return CycleResult(
            opportunity.opportunity_ref, False, str(RecoveryAction.STOP), str(decision.source),
            str(verdict.verdict), None, None, 0, False, str(opportunity.status),
            "AI chose to stop; no further recovery will be attempted",
        )

    # ---- 4. EXECUTION -------------------------------------------------
    action = verdict.effective_action
    attempt = RecoveryAttempt(
        attempt_ref=await refs.next_ref(session, "RA"),
        opportunity_id=opportunity.id,
        attempt_number=attempt_number,
        action=action,
        ai_decision_id=ai_row.id,
        policy_decision_id=policy_row.id,
        executor=executor.kind,
        execution_status=ExecutionStatus.PENDING,
        scheduled_for=utcnow(),
    )
    session.add(attempt)
    await session.flush()

    transition(opportunity, OpportunityStatus.EXECUTING)
    opportunity.attempt_count = attempt_number
    if action in NOTIFYING_ACTIONS:
        opportunity.notification_count += 1

    active_executor = NoopExecutor() if action is RecoveryAction.STOP else executor
    result = await active_executor.execute(
        opportunity=opportunity, attempt=attempt, action=action
    )

    attempt.executed_at = utcnow()
    attempt.execution_status = (
        ExecutionStatus.EXECUTED if result.executed else ExecutionStatus.EXECUTION_FAILED
    )
    attempt.external_ref = result.external_ref
    attempt.attribution_token = result.attribution_token
    attempt.error = result.error
    attempt.details = result.details

    await audit.record(
        session,
        entity_type="recovery_attempt",
        entity_id=attempt.attempt_ref,
        actor=Actor.EXECUTOR,
        action="RECOVERY_EXECUTED",
        detail={
            "action": str(action),
            "executor": str(active_executor.kind),
            "execution_status": str(attempt.execution_status),
            "external_ref": result.external_ref,
        },
        simulation_run_id=opportunity.simulation_run_id,
    )

    # ---- 5. OUTCOME ---------------------------------------------------
    if result.outcome is Outcome.PENDING:
        # Razorpay: the artifact exists, the answer arrives by webhook. The opportunity
        # stays open rather than being counted either way.
        return CycleResult(
            opportunity.opportunity_ref, True, str(action), str(decision.source),
            str(verdict.verdict), None, str(Outcome.PENDING), 0, False,
            str(opportunity.status), "Awaiting confirmation from the payment gateway",
        )

    outcome_row = RecoveryOutcome(
        attempt_id=attempt.id,
        opportunity_id=opportunity.id,
        outcome=result.outcome,
        realized_amount_minor=result.realized_amount_minor,
        evidence_type=result.evidence_type,
        evidence_ref=result.evidence_ref,
        observed_at=utcnow(),
        raw=result.details,
    )
    session.add(outcome_row)
    await session.flush()

    if result.outcome is not Outcome.SUCCESS:
        transition(opportunity, OpportunityStatus.FAILED)
        if opportunity.attempt_count >= limits.max_attempts:
            _close(opportunity, OpportunityStatus.EXHAUSTED)
        return CycleResult(
            opportunity.opportunity_ref, True, str(action), str(decision.source),
            str(verdict.verdict), None, str(result.outcome), 0, False,
            str(opportunity.status), "Recovery attempt did not succeed",
        )

    # ---- 6. LEDGER (the only writer of money) -------------------------
    transition(opportunity, OpportunityStatus.SUCCESS)
    settlement = await settle_recovery(
        session, opportunity=opportunity, attempt=attempt, outcome=outcome_row
    )

    return CycleResult(
        opportunity.opportunity_ref, True, str(action), str(decision.source),
        str(verdict.verdict), None, str(result.outcome),
        settlement.recovered_amount_minor, settlement.settled,
        str(opportunity.status),
        "Recovered" if settlement.settled else settlement.reason,
    )


async def run_until_resolved(
    session: AsyncSession,
    opportunity: RecoveryOpportunity,
    *,
    agent: RecoveryAgent,
    executor: RecoveryExecutor,
    limits: PolicyLimits | None = None,
    customer=None,
) -> list[CycleResult]:
    """Keep attempting until the opportunity recovers, exhausts, or stops."""
    limits = limits or policy_engine.limits_from_settings()
    results: list[CycleResult] = []
    state = CycleState()

    for _ in range(limits.max_attempts + 1):
        result = await run_cycle(
            session,
            opportunity,
            agent=agent,
            executor=executor,
            limits=limits,
            state=state,
            customer=customer,
        )
        results.append(result)
        status = OpportunityStatus(opportunity.status)
        if status in (
            OpportunityStatus.RECOVERED,
            OpportunityStatus.EXHAUSTED,
            OpportunityStatus.EXPIRED,
        ):
            break
        if result.outcome == str(Outcome.PENDING):
            break
        # A failed or blocked attempt returns to ANALYZING for the next cycle.
        if status in (OpportunityStatus.FAILED, OpportunityStatus.BLOCKED):
            continue
        break

    # An opportunity that ran out of cycles while still open is closed rather than left
    # dangling: every opportunity must reach a terminal state so the funnel adds up.
    if OpportunityStatus(opportunity.status) in (
        OpportunityStatus.FAILED,
        OpportunityStatus.BLOCKED,
    ):
        _close(opportunity, OpportunityStatus.EXHAUSTED)

    return results


# --- helpers ----------------------------------------------------------


def _skip(opportunity: RecoveryOpportunity, reason: str) -> CycleResult:
    return CycleResult(
        opportunity.opportunity_ref, False, None, None, None, None, None, 0, False,
        str(opportunity.status), reason,
    )


def _close(opportunity: RecoveryOpportunity, status: OpportunityStatus) -> None:
    transition(opportunity, status)
    opportunity.closed_at = utcnow()


def _is_hard_block(verdict) -> bool:
    """Blocks that will never clear on their own end the opportunity immediately."""
    return verdict.blocked_by_rule in {
        "P01_ALREADY_RECOVERED",
        "P02_TERMINAL",
        "P03_MAX_ATTEMPTS",
        "P04_NON_RETRYABLE_FAILURE",
        "P07_ACTION_SCENARIO_MISMATCH",
    }


async def _refused_actions(
    session: AsyncSession, opportunity: RecoveryOpportunity
) -> set[str]:
    """Actions the policy engine has already blocked for this opportunity."""
    rows = (
        await session.execute(
            select(PolicyDecision.requested_action).where(
                PolicyDecision.opportunity_id == opportunity.id,
                PolicyDecision.verdict == PolicyVerdict.BLOCKED,
            )
        )
    ).scalars().all()
    return {str(r) for r in rows}


async def _next_decision_sequence(
    session: AsyncSession, opportunity: RecoveryOpportunity
) -> int:
    from sqlalchemy import func

    existing = (
        await session.execute(
            select(func.count(AIDecision.id)).where(
                AIDecision.opportunity_id == opportunity.id
            )
        )
    ).scalar_one()
    return int(existing) + 1


async def _has_ledger_entry(session: AsyncSession, opportunity: RecoveryOpportunity) -> bool:
    found = (
        await session.execute(
            select(RecoveryLedger.id).where(
                RecoveryLedger.opportunity_id == opportunity.id,
                RecoveryLedger.entry_type == LedgerEntryType.RECOVERED,
            )
        )
    ).first()
    return found is not None


async def _minutes_since_last_attempt(
    session: AsyncSession, opportunity: RecoveryOpportunity
) -> float | None:
    last = (
        await session.execute(
            select(RecoveryAttempt.executed_at)
            .where(RecoveryAttempt.opportunity_id == opportunity.id)
            .order_by(RecoveryAttempt.attempt_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None:
        return None
    aware = last if last.tzinfo else last.replace(tzinfo=UTC)
    return max(0.0, (utcnow() - aware).total_seconds() / 60.0)


#: Re-exported so callers do not need to import from two modules.
__all__ = ["CycleResult", "ExecutionResult", "run_cycle", "run_until_resolved"]
