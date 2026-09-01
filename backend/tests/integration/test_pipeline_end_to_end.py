"""End-to-end pipeline behaviour across all three scenarios (§62/§63/§64).

These run the real orchestrator — detector, context builder, agent, policy engine,
executor, ledger — with the outcome engine pinned so the assertions are about the
pipeline rather than about luck.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.ai.agent import RecoveryAgent
from app.core.ids import utcnow
from app.db.models import (
    AIDecision,
    PolicyDecision,
    RecoveryAttempt,
    RecoveryLedger,
    RecoveryOutcome,
    SimulationGroundTruth,
)
from app.domain.enums import (
    CheckoutStatus,
    DecisionSource,
    FailureCategory,
    OpportunityStatus,
    Outcome,
    PolicyVerdict,
    RecoveryAction,
    Scenario,
)
from app.executor.simulator import SimulatorExecutor
from app.ingestion import normalizer_simulator as sim
from app.ingestion.detector import detect_opportunity
from app.pipeline.orchestrator import run_cycle, run_until_resolved
from app.policy.rules import PolicyLimits
from tests.factories import (
    make_checkout,
    make_customer,
    make_payment,
    make_renewal_failure,
    make_subscription,
)

LIMITS = PolicyLimits(max_attempts=3, max_notifications=2, max_discount_minor=1_000_000)


async def _ground_truth(session, opportunity, probs: dict[str, float], recoverable=True):
    """Pin hidden truth so the assertion is about the pipeline, not the dice."""
    best = max(probs, key=lambda k: probs[k]) if probs else "STOP"
    session.add(
        SimulationGroundTruth(
            opportunity_id=opportunity.id,
            action_success_probs=probs,
            latent_factors={},
            optimal_action=best,
            optimal_probability=probs.get(best, 0.0),
            is_recoverable=recoverable,
        )
    )
    await session.flush()


async def _failed_payment(session, *, amount=500_000, category=FailureCategory.TEMPORARY,
                          code="BANK_TIMEOUT", method="upi"):
    cust = make_customer()
    session.add(cust)
    await session.flush()
    pay = make_payment(cust, amount_minor=amount, failure_category=category,
                       failure_code=code, method=method)
    session.add(pay)
    await session.flush()
    event = sim.payment_failed_event(
        payment_ref=pay.payment_ref, customer_ref=cust.customer_ref, amount_minor=amount,
        method=method, failure_code=code, failure_category=category, occurred_at=utcnow(),
    )
    return cust, (await detect_opportunity(session, event)).opportunity


async def _total_recovered(session) -> int:
    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(RecoveryLedger.recovered_amount_minor), 0))
            )
        ).scalar_one()
    )


# --- Scenario A: failed payment (§62) -------------------------------------


async def test_failed_payment_recovers_full_amount(session, seed):
    _, opp = await _failed_payment(session, amount=500_000)
    await _ground_truth(session, opp, {"DELAYED_RETRY": 1.0, "IMMEDIATE_RETRY": 1.0,
                                       "PAYMENT_LINK": 1.0, "ALTERNATE_PAYMENT_METHOD": 1.0,
                                       "CUSTOMER_NOTIFICATION": 1.0})

    result = await run_cycle(
        session, opp, agent=RecoveryAgent(mode="heuristic"),
        executor=SimulatorExecutor(session, seed=seed), limits=LIMITS,
    )

    assert result.outcome == str(Outcome.SUCCESS)
    assert result.settled is True
    assert result.recovered_amount_minor == 500_000
    assert opp.status == OpportunityStatus.RECOVERED
    assert await _total_recovered(session) == 500_000


async def test_recovered_revenue_is_the_full_amount_not_amount_times_probability(session, seed):
    """RULE 1, stated as a test: ₹5,000 at 0.82 confidence recovers ₹5,000, not ₹4,100."""
    _, opp = await _failed_payment(session, amount=500_000)
    await _ground_truth(session, opp, {"DELAYED_RETRY": 1.0, "PAYMENT_LINK": 1.0,
                                       "IMMEDIATE_RETRY": 1.0, "ALTERNATE_PAYMENT_METHOD": 1.0,
                                       "CUSTOMER_NOTIFICATION": 1.0})

    await run_cycle(session, opp, agent=RecoveryAgent(mode="heuristic"),
                    executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    decision = (await session.execute(select(AIDecision))).scalar_one()
    assert 0.0 < float(decision.recovery_probability) < 1.0, "AI made a real prediction"
    assert opp.recovered_amount_minor == 500_000, "settlement ignores that prediction"


async def test_failed_recovery_records_no_revenue(session, seed):
    """RULE 2: an attempt that fails must leave the ledger empty."""
    _, opp = await _failed_payment(session)
    await _ground_truth(session, opp, {"DELAYED_RETRY": 0.0, "IMMEDIATE_RETRY": 0.0,
                                       "PAYMENT_LINK": 0.0, "ALTERNATE_PAYMENT_METHOD": 0.0,
                                       "CUSTOMER_NOTIFICATION": 0.0}, recoverable=False)

    await run_until_resolved(session, opp, agent=RecoveryAgent(mode="heuristic"),
                             executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    assert opp.status == OpportunityStatus.EXHAUSTED
    assert opp.recovered_amount_minor == 0
    assert await _total_recovered(session) == 0


async def test_repeated_failures_then_success_settle_once(session, seed):
    """§9 through the real pipeline: several attempts, one settlement."""
    _, opp = await _failed_payment(session, amount=500_000)
    # Low but non-zero: some attempts fail before one succeeds.
    await _ground_truth(session, opp, {"DELAYED_RETRY": 0.35, "PAYMENT_LINK": 0.35,
                                       "ALTERNATE_PAYMENT_METHOD": 0.35,
                                       "IMMEDIATE_RETRY": 0.35, "CUSTOMER_NOTIFICATION": 0.35})

    await run_until_resolved(session, opp, agent=RecoveryAgent(mode="heuristic"),
                             executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    entries = (await session.execute(select(RecoveryLedger))).scalars().all()
    assert len(entries) <= 1
    if opp.status == OpportunityStatus.RECOVERED:
        assert len(entries) == 1
        assert await _total_recovered(session) == 500_000
        assert opp.attempt_count >= 1


async def test_permanent_failure_is_never_retried(session, seed):
    """RULE 5 end to end."""
    _, opp = await _failed_payment(
        session, category=FailureCategory.PERMANENT, code="CARD_BLOCKED", method="card"
    )
    await _ground_truth(session, opp, {}, recoverable=False)

    await run_until_resolved(session, opp, agent=RecoveryAgent(mode="heuristic"),
                             executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    attempts = (await session.execute(select(RecoveryAttempt))).scalars().all()
    assert all(
        a.action not in (RecoveryAction.IMMEDIATE_RETRY, RecoveryAction.DELAYED_RETRY)
        for a in attempts
    )
    assert opp.status == OpportunityStatus.EXHAUSTED
    assert await _total_recovered(session) == 0


async def test_attempts_never_exceed_the_policy_limit(session, seed):
    _, opp = await _failed_payment(session)
    await _ground_truth(session, opp, {"DELAYED_RETRY": 0.0, "PAYMENT_LINK": 0.0,
                                       "ALTERNATE_PAYMENT_METHOD": 0.0, "IMMEDIATE_RETRY": 0.0,
                                       "CUSTOMER_NOTIFICATION": 0.0})

    await run_until_resolved(session, opp, agent=RecoveryAgent(mode="heuristic"),
                             executor=SimulatorExecutor(session, seed=seed),
                             limits=PolicyLimits(max_attempts=2, max_notifications=5))

    assert opp.attempt_count <= 2


# --- Scenario B: checkout abandonment (§63) -------------------------------


async def test_abandoned_checkout_recovery_is_attributed_to_that_checkout(session, seed):
    """§40: the recovering payment must point back at the cart it recovered."""
    cust = make_customer()
    session.add(cust)
    await session.flush()
    chk = make_checkout(cust, cart_value_minor=700_000)
    session.add(chk)
    await session.flush()

    event = sim.checkout_abandoned_event(
        checkout_ref=chk.checkout_ref, customer_ref=cust.customer_ref,
        cart_value_minor=700_000, product_count=2, occurred_at=utcnow(),
        intended_method="card", abandonment_reason="PAYMENT_FRICTION",
    )
    opp = (await detect_opportunity(session, event)).opportunity
    await _ground_truth(session, opp, {"PAYMENT_LINK": 1.0, "CHECKOUT_RESUME": 1.0,
                                       "REMINDER": 1.0, "DISCOUNT_INCENTIVE": 1.0,
                                       "ALTERNATE_PAYMENT_METHOD": 1.0,
                                       "CUSTOMER_NOTIFICATION": 1.0})

    result = await run_cycle(session, opp, agent=RecoveryAgent(mode="heuristic"),
                             executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    assert result.settled is True
    assert opp.recovered_amount_minor == 700_000

    from app.db.models import Payment

    payment = (
        await session.execute(
            select(Payment).where(Payment.recovers_opportunity_id == opp.id)
        )
    ).scalar_one()
    assert payment.is_recovery_payment is True
    assert payment.amount_minor == 700_000

    await session.refresh(chk)
    assert chk.status == CheckoutStatus.RECOVERED
    assert chk.completed_payment_id == payment.id


# --- Scenario C: failed subscription (§64) --------------------------------


async def test_subscription_recovery_counts_one_renewal_only(session, seed):
    """RULE 10: recovering a ₹999 renewal recovers ₹999 — not twelve months of it."""
    cust = make_customer()
    session.add(cust)
    await session.flush()
    sub = make_subscription(cust, amount_minor=99_900)
    session.add(sub)
    await session.flush()
    ren = make_renewal_failure(sub, cust)
    session.add(ren)
    await session.flush()

    event = sim.subscription_failed_event(
        renewal_ref=ren.renewal_ref, subscription_ref=sub.subscription_ref,
        customer_ref=cust.customer_ref, amount_minor=99_900, method="card",
        failure_code="CARD_EXPIRED", failure_category=FailureCategory.EXPIRED_CARD,
        occurred_at=utcnow(),
    )
    opp = (await detect_opportunity(session, event)).opportunity
    await _ground_truth(session, opp, {"PAYMENT_UPDATE_REQUEST": 1.0, "PAYMENT_LINK": 1.0,
                                       "ALTERNATE_PAYMENT_METHOD": 1.0,
                                       "CUSTOMER_NOTIFICATION": 1.0, "GRACE_PERIOD": 1.0})

    result = await run_cycle(session, opp, agent=RecoveryAgent(mode="heuristic"),
                             executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    assert result.settled is True
    assert opp.recovered_amount_minor == 99_900
    assert opp.projected_retention_minor == 99_900 * 12
    assert await _total_recovered(session) == 99_900, "projected value is not recovered revenue"

    from app.db.models import SubscriptionEvent

    recovery_renewal = (
        await session.execute(
            select(SubscriptionEvent).where(
                SubscriptionEvent.recovers_opportunity_id == opp.id
            )
        )
    ).scalar_one()
    assert recovery_renewal.is_recovery_renewal is True
    assert recovery_renewal.amount_minor == 99_900


async def test_expired_card_subscription_asks_for_a_new_payment_method(session, seed):
    """A dead card is not fixed by retrying; the pipeline must change the instrument."""
    cust = make_customer()
    session.add(cust)
    await session.flush()
    sub = make_subscription(cust, payment_method="card")
    session.add(sub)
    await session.flush()
    ren = make_renewal_failure(sub, cust, failure_code="CARD_EXPIRED",
                               failure_category=FailureCategory.EXPIRED_CARD)
    session.add(ren)
    await session.flush()

    event = sim.subscription_failed_event(
        renewal_ref=ren.renewal_ref, subscription_ref=sub.subscription_ref,
        customer_ref=cust.customer_ref, amount_minor=sub.amount_minor, method="card",
        failure_code="CARD_EXPIRED", failure_category=FailureCategory.EXPIRED_CARD,
        occurred_at=utcnow(),
    )
    opp = (await detect_opportunity(session, event)).opportunity
    await _ground_truth(session, opp, {"PAYMENT_UPDATE_REQUEST": 0.9, "PAYMENT_LINK": 0.5})

    result = await run_cycle(session, opp, agent=RecoveryAgent(mode="heuristic"),
                             executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    assert result.action == str(RecoveryAction.PAYMENT_UPDATE_REQUEST)


# --- Pipeline integrity ---------------------------------------------------


async def test_every_execution_was_preceded_by_an_approval(session, seed):
    """RULE 4 structurally: nothing executes without a recorded APPROVED verdict."""
    _, opp = await _failed_payment(session)
    await _ground_truth(session, opp, {"DELAYED_RETRY": 0.5, "PAYMENT_LINK": 0.5,
                                       "ALTERNATE_PAYMENT_METHOD": 0.5,
                                       "IMMEDIATE_RETRY": 0.5, "CUSTOMER_NOTIFICATION": 0.5})

    await run_until_resolved(session, opp, agent=RecoveryAgent(mode="heuristic"),
                             executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    attempts = (await session.execute(select(RecoveryAttempt))).scalars().all()
    for attempt in attempts:
        assert attempt.policy_decision_id is not None
        policy = (
            await session.execute(
                select(PolicyDecision).where(PolicyDecision.id == attempt.policy_decision_id)
            )
        ).scalar_one()
        assert policy.verdict == PolicyVerdict.APPROVED


async def test_full_audit_chain_exists_for_a_recovery(session, seed):
    """§59: decision -> policy -> attempt -> outcome -> ledger, all linked."""
    _, opp = await _failed_payment(session)
    await _ground_truth(session, opp, {"DELAYED_RETRY": 1.0, "PAYMENT_LINK": 1.0,
                                       "ALTERNATE_PAYMENT_METHOD": 1.0, "IMMEDIATE_RETRY": 1.0,
                                       "CUSTOMER_NOTIFICATION": 1.0})

    await run_cycle(session, opp, agent=RecoveryAgent(mode="heuristic"),
                    executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    ledger = (await session.execute(select(RecoveryLedger))).scalar_one()
    attempt = (
        await session.execute(select(RecoveryAttempt).where(RecoveryAttempt.id == ledger.attempt_id))
    ).scalar_one()
    outcome = (
        await session.execute(select(RecoveryOutcome).where(RecoveryOutcome.id == ledger.outcome_id))
    ).scalar_one()
    decision = (
        await session.execute(select(AIDecision).where(AIDecision.id == attempt.ai_decision_id))
    ).scalar_one()
    policy = (
        await session.execute(
            select(PolicyDecision).where(PolicyDecision.id == attempt.policy_decision_id)
        )
    ).scalar_one()

    assert ledger.opportunity_id == opp.id
    assert outcome.attempt_id == attempt.id
    assert outcome.evidence_ref is not None
    assert decision.reason
    assert policy.rules_evaluated
    assert decision.decision_source == DecisionSource.HEURISTIC


async def test_context_snapshot_is_stored_without_ground_truth(session, seed):
    """The audit snapshot must be exactly what the AI saw — no hidden fields."""
    from app.domain.context import FORBIDDEN_CONTEXT_KEYS

    _, opp = await _failed_payment(session)
    await _ground_truth(session, opp, {"DELAYED_RETRY": 0.9})

    await run_cycle(session, opp, agent=RecoveryAgent(mode="heuristic"),
                    executor=SimulatorExecutor(session, seed=seed), limits=LIMITS)

    def keys(obj, acc=None):
        acc = acc if acc is not None else set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                acc.add(k)
                keys(v, acc)
        elif isinstance(obj, list):
            for i in obj:
                keys(i, acc)
        return acc

    assert opp.context_snapshot
    assert not (keys(opp.context_snapshot) & FORBIDDEN_CONTEXT_KEYS)


@pytest.mark.parametrize("scenario", list(Scenario))
async def test_all_three_scenarios_are_supported(session, scenario):
    """Acceptance criterion 1: every scenario has a defined action set."""
    from app.domain.enums import SCENARIO_ACTIONS

    assert SCENARIO_ACTIONS[scenario]
    assert RecoveryAction.STOP in SCENARIO_ACTIONS[scenario]
