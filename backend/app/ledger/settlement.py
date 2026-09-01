"""Recovery settlement — the only place recovered revenue is ever created.

This module is the enforcement point for the platform's central business rules:

  RULE 1  An AI probability is not recovered revenue. Nothing here reads a
          probability, a confidence, or an expected value.
  RULE 2  Only an observed SUCCESS outcome creates recovered revenue.
  RULE 3  One opportunity contributes recovered revenue at most once, ever.
  RULE 9  Every settled rupee traces back to an opportunity, an attempt and the
          evidence that proved it.

Idempotency is defended three times over: an in-transaction status check under a row
lock, a UNIQUE settlement key, and a partial unique index on the ledger. A duplicate
webhook, a replayed outcome, or two concurrent workers all converge on one entry.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.ids import utcnow
from app.core.logging import get_logger
from app.db.models import RecoveryAttempt, RecoveryLedger, RecoveryOpportunity, RecoveryOutcome
from app.domain.enums import Actor, LedgerEntryType, OpportunityStatus, Outcome
from app.domain.state_machine import transition
from app.services import audit

log = get_logger("recoverai.ledger")


@dataclass(slots=True)
class SettlementResult:
    settled: bool
    already_settled: bool
    recovered_amount_minor: int
    ledger_entry_id: str | None
    reason: str


def settlement_key_for(opportunity: RecoveryOpportunity, outcome: RecoveryOutcome) -> str:
    """Stable key over the *evidence*, so replaying the same proof is a no-op."""
    evidence = outcome.evidence_ref or str(outcome.id)
    return f"{opportunity.opportunity_ref}:{evidence}"


async def settle_recovery(
    session: AsyncSession,
    *,
    opportunity: RecoveryOpportunity,
    attempt: RecoveryAttempt,
    outcome: RecoveryOutcome,
) -> SettlementResult:
    """Record realized recovered revenue for a successful recovery attempt."""

    if outcome.outcome != Outcome.SUCCESS:
        return SettlementResult(False, False, 0, None, "outcome_not_success")

    amount = int(outcome.realized_amount_minor or 0)
    if amount <= 0:
        raise ValidationError(
            "A successful recovery outcome must carry a positive realized amount",
            details={"opportunity_ref": opportunity.opportunity_ref, "amount_minor": amount},
        )
    if amount > opportunity.amount_at_risk_minor:
        # Never let a recovery claim more than was ever at risk (§39, RULE 10).
        raise ValidationError(
            "Realized amount exceeds the amount originally at risk",
            details={
                "opportunity_ref": opportunity.opportunity_ref,
                "amount_minor": amount,
                "amount_at_risk_minor": opportunity.amount_at_risk_minor,
            },
        )

    # Serialize concurrent settlements of the same opportunity (no-op on SQLite).
    locked = (
        await session.execute(
            select(RecoveryOpportunity)
            .where(RecoveryOpportunity.id == opportunity.id)
            .with_for_update()
        )
    ).scalar_one()

    if locked.status == OpportunityStatus.RECOVERED:
        return SettlementResult(
            False, True, int(locked.recovered_amount_minor), None, "already_recovered"
        )

    existing = (
        await session.execute(
            select(RecoveryLedger).where(
                RecoveryLedger.opportunity_id == opportunity.id,
                RecoveryLedger.entry_type == LedgerEntryType.RECOVERED,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return SettlementResult(
            False, True, int(existing.recovered_amount_minor), str(existing.id), "ledger_entry_exists"
        )

    entry = RecoveryLedger(
        entry_type=LedgerEntryType.RECOVERED,
        opportunity_id=opportunity.id,
        outcome_id=outcome.id,
        attempt_id=attempt.id,
        simulation_run_id=opportunity.simulation_run_id,
        scenario=opportunity.scenario,
        original_amount_minor=opportunity.amount_at_risk_minor,
        recovered_amount_minor=amount,
        currency=opportunity.currency,
        source=opportunity.source,
        action=attempt.action,
        attempt_number=attempt.attempt_number,
        settlement_key=settlement_key_for(opportunity, outcome),
        settled_at=utcnow(),
    )
    try:
        # SAVEPOINT: if another worker settled first, undo only this insert. Rolling back
        # the whole transaction would throw away unrelated committed-in-progress work.
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except IntegrityError:
        # Lost a race, or the same evidence was replayed. Both mean: already settled.
        # The savepoint rollback usually evicts it already; expunge only if it lingers.
        if entry in session:
            session.expunge(entry)
        log.info(
            "ledger.duplicate_settlement_prevented",
            opportunity_ref=opportunity.opportunity_ref,
        )
        return SettlementResult(False, True, 0, None, "duplicate_settlement_prevented")

    # Only now does the opportunity become RECOVERED.
    if locked.status != OpportunityStatus.SUCCESS:
        transition(locked, OpportunityStatus.SUCCESS)
    transition(locked, OpportunityStatus.RECOVERED)
    locked.recovered_amount_minor = amount
    locked.recovered_at = entry.settled_at
    locked.closed_at = entry.settled_at

    await audit.record(
        session,
        entity_type="recovery_opportunity",
        entity_id=opportunity.opportunity_ref,
        actor=Actor.LEDGER,
        action="RECOVERY_SETTLED",
        detail={
            "scenario": str(opportunity.scenario),
            "recovered_amount_minor": amount,
            "attempt_ref": attempt.attempt_ref,
            "action": str(attempt.action),
            "evidence_type": str(outcome.evidence_type),
            "evidence_ref": outcome.evidence_ref,
            "settlement_key": entry.settlement_key,
        },
        simulation_run_id=opportunity.simulation_run_id,
    )

    log.info(
        "ledger.settled",
        opportunity_ref=opportunity.opportunity_ref,
        scenario=str(opportunity.scenario),
        recovered_amount_minor=amount,
    )
    return SettlementResult(True, False, amount, str(entry.id), "settled")
