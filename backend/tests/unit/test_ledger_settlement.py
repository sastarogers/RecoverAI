"""The double-counting guarantee (§9, RULE 3) and the 'AI does not create money' rule."""

import pytest
from sqlalchemy import func, select

from app.core.errors import ValidationError
from app.core.ids import utcnow
from app.db.models import RecoveryAttempt, RecoveryLedger, RecoveryOutcome
from app.domain.enums import (
    EvidenceType,
    ExecutionStatus,
    ExecutorKind,
    OpportunityStatus,
    Outcome,
    RecoveryAction,
)
from app.domain.state_machine import transition
from app.ledger.settlement import settle_recovery
from tests.factories import make_customer, make_opportunity


async def _prepare(session, *, amount=500_000):
    cust = make_customer()
    session.add(cust)
    await session.flush()
    opp = make_opportunity(cust, amount_minor=amount)
    opp.customer_id = cust.id
    session.add(opp)
    await session.flush()
    return cust, opp


def _advance_to_executing(opp):
    """Walk the legal pipeline path an executor would have walked."""
    for target in (
        OpportunityStatus.ANALYZING,
        OpportunityStatus.RECOMMENDED,
        OpportunityStatus.APPROVED,
        OpportunityStatus.EXECUTING,
    ):
        transition(opp, target)


async def _attempt(session, opp, n: int, action=RecoveryAction.DELAYED_RETRY, advance=True):
    if advance:
        _advance_to_executing(opp)
    att = RecoveryAttempt(
        attempt_ref=f"RA{n:04d}",
        opportunity_id=opp.id,
        attempt_number=n,
        action=action,
        executor=ExecutorKind.SIMULATOR,
        execution_status=ExecutionStatus.EXECUTED,
        executed_at=utcnow(),
    )
    session.add(att)
    await session.flush()
    return att


async def _outcome(session, opp, att, result: Outcome, amount: int, evidence: str):
    out = RecoveryOutcome(
        attempt_id=att.id,
        opportunity_id=opp.id,
        outcome=result,
        realized_amount_minor=amount if result == Outcome.SUCCESS else 0,
        evidence_type=EvidenceType.SIMULATED_GROUND_TRUTH,
        evidence_ref=evidence,
        observed_at=utcnow(),
    )
    session.add(out)
    await session.flush()
    return out


async def _ledger_total(session) -> int:
    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(RecoveryLedger.recovered_amount_minor), 0))
            )
        ).scalar_one()
    )


async def test_successful_recovery_settles_full_amount(session):
    _, opp = await _prepare(session, amount=500_000)
    att = await _attempt(session, opp, 1)
    out = await _outcome(session, opp, att, Outcome.SUCCESS, 500_000, "P0002")

    result = await settle_recovery(session, opportunity=opp, attempt=att, outcome=out)

    assert result.settled is True
    assert result.recovered_amount_minor == 500_000
    assert opp.status == OpportunityStatus.RECOVERED
    assert opp.recovered_amount_minor == 500_000
    assert await _ledger_total(session) == 500_000


async def test_failed_outcome_creates_no_revenue(session):
    """RULE 2: a failed recovery attempt must never move money."""
    _, opp = await _prepare(session)
    att = await _attempt(session, opp, 1)
    out = await _outcome(session, opp, att, Outcome.FAILURE, 0, "none")

    result = await settle_recovery(session, opportunity=opp, attempt=att, outcome=out)

    assert result.settled is False
    assert result.reason == "outcome_not_success"
    assert opp.recovered_amount_minor == 0
    assert await _ledger_total(session) == 0


async def test_three_attempts_then_success_settles_once(session):
    """§9 verbatim: fail, fail, succeed on ₹5,000 must yield ₹5,000 — not ₹15,000."""
    _, opp = await _prepare(session, amount=500_000)

    for n in (1, 2):
        att = await _attempt(session, opp, n)
        out = await _outcome(session, opp, att, Outcome.FAILURE, 0, f"fail-{n}")
        assert (
            await settle_recovery(session, opportunity=opp, attempt=att, outcome=out)
        ).settled is False
        transition(opp, OpportunityStatus.FAILED)

    att3 = await _attempt(session, opp, 3)
    out3 = await _outcome(session, opp, att3, Outcome.SUCCESS, 500_000, "P0004")
    assert (await settle_recovery(session, opportunity=opp, attempt=att3, outcome=out3)).settled is True

    assert await _ledger_total(session) == 500_000
    entries = (await session.execute(select(RecoveryLedger))).scalars().all()
    assert len(entries) == 1


async def test_replaying_the_same_outcome_is_a_noop(session):
    """A duplicate webhook delivering the same proof twice settles once."""
    _, opp = await _prepare(session)
    att = await _attempt(session, opp, 1)
    out = await _outcome(session, opp, att, Outcome.SUCCESS, 500_000, "pay_ABC123")

    first = await settle_recovery(session, opportunity=opp, attempt=att, outcome=out)
    second = await settle_recovery(session, opportunity=opp, attempt=att, outcome=out)

    assert first.settled is True
    assert second.settled is False and second.already_settled is True
    assert await _ledger_total(session) == 500_000


async def test_second_success_on_recovered_opportunity_is_rejected(session):
    """Even a *different* successful payment cannot settle an already-recovered opportunity."""
    _, opp = await _prepare(session)
    att1 = await _attempt(session, opp, 1)
    out1 = await _outcome(session, opp, att1, Outcome.SUCCESS, 500_000, "pay_FIRST")
    await settle_recovery(session, opportunity=opp, attempt=att1, outcome=out1)

    att2 = await _attempt(session, opp, 2, advance=False)
    out2 = await _outcome(session, opp, att2, Outcome.SUCCESS, 500_000, "pay_SECOND")
    second = await settle_recovery(session, opportunity=opp, attempt=att2, outcome=out2)

    assert second.already_settled is True
    assert await _ledger_total(session) == 500_000


async def test_cannot_settle_more_than_amount_at_risk(session):
    """No expected-value inflation: recovery is capped at what was actually at risk."""
    _, opp = await _prepare(session, amount=500_000)
    att = await _attempt(session, opp, 1)
    out = await _outcome(session, opp, att, Outcome.SUCCESS, 900_000, "pay_TOOBIG")

    with pytest.raises(ValidationError):
        await settle_recovery(session, opportunity=opp, attempt=att, outcome=out)
    assert await _ledger_total(session) == 0


async def test_ledger_entry_traces_back_to_its_evidence(session):
    """RULE 9: every settled rupee is traceable to opportunity, attempt and proof."""
    _, opp = await _prepare(session)
    att = await _attempt(session, opp, 1, action=RecoveryAction.PAYMENT_LINK)
    out = await _outcome(session, opp, att, Outcome.SUCCESS, 500_000, "pay_TRACE")
    await settle_recovery(session, opportunity=opp, attempt=att, outcome=out)

    entry = (await session.execute(select(RecoveryLedger))).scalar_one()
    assert entry.opportunity_id == opp.id
    assert entry.attempt_id == att.id
    assert entry.outcome_id == out.id
    assert entry.action == RecoveryAction.PAYMENT_LINK
    assert entry.original_amount_minor == opp.amount_at_risk_minor
    assert entry.settlement_key == "OPP0001:pay_TRACE"
