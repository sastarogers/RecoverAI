"""Simulator executor.

Resolves an attempt against the hidden ground truth for its opportunity. This is the
first point in the whole pipeline where ground truth is read, and by then the AI
decision and the policy verdict are already persisted (§18).

A success here also creates the *real artifact* that proves it — a recovery payment row
or a recovery renewal row, linked back to the opportunity — so recovered revenue is
traceable to concrete evidence rather than to a boolean (RULE 9).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import utcnow
from app.core.logging import get_logger
from app.db.models import (
    CheckoutSession,
    Payment,
    RecoveryAttempt,
    RecoveryOpportunity,
    SimulationGroundTruth,
    Subscription,
    SubscriptionEvent,
)
from app.domain.enums import (
    CheckoutStatus,
    EvidenceType,
    ExecutorKind,
    Outcome,
    PaymentStatus,
    RecoveryAction,
    Scenario,
    Source,
    SubscriptionEventType,
    SubscriptionStatus,
)
from app.executor.base import ExecutionResult, RecoveryExecutor
from app.services import refs
from app.simulation.outcome import draw_outcome, reproducibility_key

log = get_logger("recoverai.executor.simulator")


class SimulatorExecutor(RecoveryExecutor):
    kind = ExecutorKind.SIMULATOR

    def __init__(
        self,
        session: AsyncSession,
        *,
        seed: int,
        ground_truth: dict | None = None,
    ) -> None:
        self.session = session
        self.seed = seed
        #: opportunity_id -> SimulationGroundTruth, prefetched per chunk by the runner so
        #: the pipeline does not issue one SELECT per attempt.
        self._ground_truth = ground_truth if ground_truth is not None else {}

    async def execute(
        self,
        *,
        opportunity: RecoveryOpportunity,
        attempt: RecoveryAttempt,
        action: RecoveryAction,
    ) -> ExecutionResult:
        truth = self._ground_truth.get(opportunity.id)
        if truth is None:
            truth = (
                await self.session.execute(
                    select(SimulationGroundTruth).where(
                        SimulationGroundTruth.opportunity_id == opportunity.id
                    )
                )
            ).scalar_one_or_none()

        if truth is None:
            return ExecutionResult(
                executed=False,
                outcome=Outcome.NO_RESPONSE,
                error="no ground truth for this opportunity",
            )

        draw = draw_outcome(
            seed=self.seed,
            opportunity_ref=reproducibility_key(opportunity),
            attempt_number=attempt.attempt_number,
            action=action,
            action_success_probs=dict(truth.action_success_probs),
        )

        if draw.outcome is not Outcome.SUCCESS:
            return ExecutionResult(
                executed=True,
                outcome=draw.outcome,
                details={"true_probability": draw.true_probability, "roll": draw.roll},
            )

        evidence_ref = await self._materialize_success(opportunity, attempt, action)
        return ExecutionResult(
            executed=True,
            outcome=Outcome.SUCCESS,
            realized_amount_minor=int(opportunity.amount_at_risk_minor),
            evidence_type=EvidenceType.SIMULATED_GROUND_TRUTH,
            evidence_ref=evidence_ref,
            details={"true_probability": draw.true_probability, "roll": draw.roll},
        )

    async def _materialize_success(
        self,
        opportunity: RecoveryOpportunity,
        attempt: RecoveryAttempt,
        action: RecoveryAction,
    ) -> str:
        """Create the artifact that proves the recovery, attributed to the opportunity."""
        scenario = Scenario(opportunity.scenario)
        now = utcnow()

        if scenario is Scenario.FAILED_SUBSCRIPTION:
            renewal_ref = await refs.next_ref(self.session, "REN-R")
            event = SubscriptionEvent(
                renewal_ref=renewal_ref,
                subscription_id=opportunity.subscription_id,
                customer_id=opportunity.customer_id,
                cycle_number=0,
                event_type=SubscriptionEventType.RENEWAL_SUCCESS,
                amount_minor=opportunity.amount_at_risk_minor,
                is_recovery_renewal=True,
                recovers_opportunity_id=opportunity.id,
                occurred_at=now,
                notes=f"Recovered via {action} ({attempt.attempt_ref})",
            )
            self.session.add(event)
            subscription = (
                await self.session.execute(
                    select(Subscription).where(Subscription.id == opportunity.subscription_id)
                )
            ).scalar_one_or_none()
            if subscription is not None:
                subscription.status = SubscriptionStatus.ACTIVE
                subscription.previous_successful_renewals += 1
            await self.session.flush()
            return renewal_ref

        # Failed payment and abandoned checkout both settle through a real payment.
        payment_ref = await refs.next_ref(self.session, "P-R")
        payment = Payment(
            payment_ref=payment_ref,
            customer_id=opportunity.customer_id,
            amount_minor=opportunity.amount_at_risk_minor,
            currency=opportunity.currency,
            method=await self._method_for(opportunity, action),
            status=PaymentStatus.CAPTURED,
            source=Source.SIMULATOR,
            simulation_run_id=opportunity.simulation_run_id,
            is_recovery_payment=True,
            recovers_opportunity_id=opportunity.id,
            occurred_at=now,
            payment_metadata={
                "recovery_action": str(action),
                "attempt_ref": attempt.attempt_ref,
                "recovers_opportunity_ref": opportunity.opportunity_ref,
            },
        )
        self.session.add(payment)
        await self.session.flush()

        if scenario is Scenario.CHECKOUT_ABANDONMENT and opportunity.checkout_session_id:
            # §40 attribution: this payment recovered *this* checkout, not a coincidental
            # later purchase.
            checkout = (
                await self.session.execute(
                    select(CheckoutSession).where(
                        CheckoutSession.id == opportunity.checkout_session_id
                    )
                )
            ).scalar_one_or_none()
            if checkout is not None:
                checkout.status = CheckoutStatus.RECOVERED
                checkout.completed_payment_id = payment.id
                checkout.completed_at = now

        return payment_ref

    async def _method_for(
        self, opportunity: RecoveryOpportunity, action: RecoveryAction
    ) -> str | None:
        if action is RecoveryAction.ALTERNATE_PAYMENT_METHOD:
            return "upi"
        if opportunity.payment_id:
            original = (
                await self.session.execute(
                    select(Payment).where(Payment.id == opportunity.payment_id)
                )
            ).scalar_one_or_none()
            if original is not None:
                return original.method
        return None
