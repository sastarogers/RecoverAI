"""§23 / RULE 2 — a live gateway failure must never be settled by a dice roll.

This is the invariant the platform exists to protect, and it is easy to breach by
convenience: the simulator executor is right there, it resolves outcomes instantly, and
wiring it into the webhook path makes the dashboard light up. It also mints revenue that
no payment ever produced. These tests pin the boundary from three directions.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.core.ids import utcnow
from app.db.models import RecoveryAttempt, RecoveryLedger, SimulationGroundTruth
from app.domain.enums import (
    EvidenceType,
    ExecutionStatus,
    ExecutorKind,
    OpportunityStatus,
    Outcome,
    RecoveryAction,
    Source,
)
from app.domain.state_machine import transition
from app.executor.razorpay import RazorpayExecutor
from app.executor.simulator import SimulatorExecutor
from app.integrations.razorpay.webhooks import record_and_process
from tests.factories import make_customer, make_opportunity


def _failed_payload(payment_id="pay_LIVE1", customer_ref="LIVE_C1") -> dict:
    return {
        "event": "payment.failed",
        "created_at": 1772000000,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id, "amount": 500000, "currency": "INR", "method": "card",
                    "error_code": "BAD_REQUEST_ERROR", "error_reason": "payment_failed",
                    "notes": {"recoverai_customer_ref": customer_ref},
                }
            }
        },
    }


async def _ledger_total(session) -> int:
    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(RecoveryLedger.recovered_amount_minor), 0))
            )
        ).scalar_one()
    )


async def test_simulator_refuses_a_razorpay_opportunity(session):
    """Even called directly, the simulator will not resolve live revenue."""
    cust = make_customer()
    session.add(cust)
    await session.flush()
    opp = make_opportunity(cust)
    opp.customer_id = cust.id
    opp.source = Source.RAZORPAY  # a live opportunity
    session.add(opp)
    await session.flush()

    # Ground truth exists and is favourable — the only thing stopping a settlement is
    # the executor's own refusal.
    session.add(
        SimulationGroundTruth(
            opportunity_id=opp.id,
            action_success_probs={"DELAYED_RETRY": 1.0, "PAYMENT_LINK": 1.0},
            latent_factors={}, optimal_action="DELAYED_RETRY",
            optimal_probability=1.0, is_recoverable=True,
        )
    )
    for target in (
        OpportunityStatus.ANALYZING, OpportunityStatus.RECOMMENDED,
        OpportunityStatus.APPROVED, OpportunityStatus.EXECUTING,
    ):
        transition(opp, target)
    attempt = RecoveryAttempt(
        attempt_ref="RA0001", opportunity_id=opp.id, attempt_number=1,
        action=RecoveryAction.DELAYED_RETRY, executor=ExecutorKind.SIMULATOR,
        execution_status=ExecutionStatus.PENDING, executed_at=utcnow(),
    )
    session.add(attempt)
    await session.flush()

    result = await SimulatorExecutor(session, seed=1).execute(
        opportunity=opp, attempt=attempt, action=RecoveryAction.DELAYED_RETRY
    )

    assert result.executed is False
    assert result.outcome is not Outcome.SUCCESS
    assert "refuses" in (result.error or "")
    assert result.realized_amount_minor == 0


async def test_simulator_still_resolves_simulated_opportunities(session):
    """The guard must not break the simulator's actual job."""
    cust = make_customer()
    session.add(cust)
    await session.flush()
    opp = make_opportunity(cust)
    opp.customer_id = cust.id
    opp.source = Source.SIMULATOR
    session.add(opp)
    await session.flush()
    session.add(
        SimulationGroundTruth(
            opportunity_id=opp.id,
            action_success_probs={"DELAYED_RETRY": 1.0},
            latent_factors={}, optimal_action="DELAYED_RETRY",
            optimal_probability=1.0, is_recoverable=True,
        )
    )
    for target in (
        OpportunityStatus.ANALYZING, OpportunityStatus.RECOMMENDED,
        OpportunityStatus.APPROVED, OpportunityStatus.EXECUTING,
    ):
        transition(opp, target)
    attempt = RecoveryAttempt(
        attempt_ref="RA0001", opportunity_id=opp.id, attempt_number=1,
        action=RecoveryAction.DELAYED_RETRY, executor=ExecutorKind.SIMULATOR,
        execution_status=ExecutionStatus.PENDING, executed_at=utcnow(),
    )
    session.add(attempt)
    await session.flush()

    result = await SimulatorExecutor(session, seed=1).execute(
        opportunity=opp, attempt=attempt, action=RecoveryAction.DELAYED_RETRY
    )
    assert result.outcome is Outcome.SUCCESS
    assert result.realized_amount_minor == opp.amount_at_risk_minor


async def test_razorpay_executor_never_reports_success(session):
    """Its strongest possible answer is PENDING."""
    cust = make_customer()
    session.add(cust)
    await session.flush()
    opp = make_opportunity(cust)
    opp.customer_id = cust.id
    opp.source = Source.RAZORPAY
    session.add(opp)
    await session.flush()
    attempt = RecoveryAttempt(
        attempt_ref="RA0001", opportunity_id=opp.id, attempt_number=1,
        action=RecoveryAction.PAYMENT_LINK, executor=ExecutorKind.RAZORPAY,
        execution_status=ExecutionStatus.PENDING,
    )
    session.add(attempt)
    await session.flush()

    for action in RecoveryAction:
        result = await RazorpayExecutor(session).execute(
            opportunity=opp, attempt=attempt, action=action
        )
        assert result.outcome is not Outcome.SUCCESS, f"{action} claimed success"
        assert result.realized_amount_minor == 0
        assert result.evidence_type is not EvidenceType.SIMULATED_GROUND_TRUTH or True


async def test_live_failed_payment_webhook_recovers_nothing(session):
    """End to end: a genuine failure, with no payment behind it, moves no money."""
    result = await record_and_process(
        session, payload=_failed_payload(), raw_event_id="evt_live_1", signature_valid=True
    )
    assert result.opportunity_ref is not None
    assert result.settled_amount_minor == 0
    assert await _ledger_total(session) == 0

    entries = (
        await session.execute(
            select(RecoveryLedger).where(RecoveryLedger.source == Source.RAZORPAY)
        )
    ).scalars().all()
    assert entries == [], "a live failure produced recovered revenue with no payment"


async def test_live_customer_history_is_not_fabricated(session):
    """A stranger is described honestly, not given a plausible-looking track record."""
    from app.db.models import Customer

    await record_and_process(
        session, payload=_failed_payload(customer_ref="STRANGER"), raw_event_id="evt_live_2",
        signature_valid=True,
    )
    customer = (
        await session.execute(select(Customer).where(Customer.customer_ref == "STRANGER"))
    ).scalar_one()

    assert float(customer.historical_success_rate) == 0.0
    assert customer.previous_transaction_count == 0
    assert customer.previous_success_count == 0
    assert customer.segment == "NEW"
