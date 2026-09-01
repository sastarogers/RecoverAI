"""Baseline comparison by counterfactual replay (§36).

Each baseline is replayed over **the same opportunities** and **the same hidden ground
truth** as the real run, using the same seed-derived outcome draws. Because
`draw_outcome` keys its randomness on (seed, opportunity_ref, attempt_number) and not on
call order, a baseline and RecoverAI facing the same opportunity on the same attempt get
the *same dice roll* — they differ only in which action they chose.

That is a paired comparison (common random numbers), so the measured uplift reflects
decision quality rather than luck. Baselines deliberately bypass the policy engine: the
point of the comparison is to show what the guardrails and the decision-making are worth.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    BaselineResult,
    RecoveryLedger,
    RecoveryOpportunity,
    SimulationGroundTruth,
)
from app.domain.enums import (
    NOTIFYING_ACTIONS,
    BaselineStrategy,
    LedgerEntryType,
    Outcome,
    RecoveryAction,
    Scenario,
)
from app.simulation.outcome import draw_outcome, reproducibility_key

log = get_logger("recoverai.baselines")

#: "Just retry it" — the naive strategy, expressed per scenario.
ALWAYS_RETRY_ACTION: dict[Scenario, RecoveryAction] = {
    Scenario.FAILED_PAYMENT: RecoveryAction.IMMEDIATE_RETRY,
    Scenario.FAILED_SUBSCRIPTION: RecoveryAction.RETRY_SUBSCRIPTION,
    # A cart has nothing to retry, so the equivalent naive move is another nudge.
    Scenario.CHECKOUT_ABANDONMENT: RecoveryAction.REMINDER,
}

#: A conventional static dunning ladder: try again, then tell the customer, then stop.
FIXED_RETRY_LADDER: dict[Scenario, tuple[RecoveryAction, ...]] = {
    Scenario.FAILED_PAYMENT: (
        RecoveryAction.DELAYED_RETRY,
        RecoveryAction.CUSTOMER_NOTIFICATION,
    ),
    Scenario.FAILED_SUBSCRIPTION: (
        RecoveryAction.DELAYED_RETRY,
        RecoveryAction.CUSTOMER_NOTIFICATION,
    ),
    Scenario.CHECKOUT_ABANDONMENT: (
        RecoveryAction.PAYMENT_LINK,
        RecoveryAction.REMINDER,
    ),
}


@dataclass(slots=True)
class StrategyMetrics:
    strategy: str
    revenue_at_risk_minor: int = 0
    recovered_revenue_minor: int = 0
    opportunities: int = 0
    opportunities_recovered: int = 0
    attempts: int = 0
    #: Attempts spent on opportunities that were never recoverable — pure waste.
    unnecessary_attempts: int = 0
    customer_notifications: int = 0
    blocked_actions: int = 0
    per_scenario: dict = field(default_factory=dict)

    @property
    def recovery_rate(self) -> float:
        return (
            self.recovered_revenue_minor / self.revenue_at_risk_minor
            if self.revenue_at_risk_minor
            else 0.0
        )

    @property
    def avg_attempts_per_recovery(self) -> float:
        return self.attempts / self.opportunities_recovered if self.opportunities_recovered else 0.0

    @property
    def recovery_efficiency_minor(self) -> float:
        """Rupees recovered per attempt made."""
        return self.recovered_revenue_minor / self.attempts if self.attempts else 0.0

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "revenue_at_risk_minor": self.revenue_at_risk_minor,
            "recovered_revenue_minor": self.recovered_revenue_minor,
            "recovery_rate": round(self.recovery_rate, 4),
            "opportunities": self.opportunities,
            "opportunities_recovered": self.opportunities_recovered,
            "attempts": self.attempts,
            "unnecessary_attempts": self.unnecessary_attempts,
            "customer_notifications": self.customer_notifications,
            "blocked_actions": self.blocked_actions,
            "avg_attempts_per_recovery": round(self.avg_attempts_per_recovery, 3),
            "recovery_efficiency_minor": round(self.recovery_efficiency_minor, 2),
            "per_scenario": self.per_scenario,
        }


def _actions_for(strategy: BaselineStrategy, scenario: Scenario, max_attempts: int):
    if strategy is BaselineStrategy.NO_RECOVERY:
        return ()
    if strategy is BaselineStrategy.ALWAYS_RETRY:
        return tuple([ALWAYS_RETRY_ACTION[scenario]] * max_attempts)
    if strategy is BaselineStrategy.FIXED_RETRY:
        return FIXED_RETRY_LADDER[scenario][:max_attempts]
    return ()


def replay_strategy(
    strategy: BaselineStrategy,
    rows: list[tuple[RecoveryOpportunity, SimulationGroundTruth]],
    *,
    seed: int,
    max_attempts: int,
) -> StrategyMetrics:
    """Replay one baseline over the run's opportunities and hidden truth."""
    metrics = StrategyMetrics(strategy=str(strategy))
    per_scenario: dict[str, dict[str, int]] = {}

    for opportunity, truth in rows:
        scenario = Scenario(opportunity.scenario)
        at_risk = int(opportunity.amount_at_risk_minor)
        bucket = per_scenario.setdefault(
            str(scenario),
            {"revenue_at_risk_minor": 0, "recovered_revenue_minor": 0,
             "opportunities": 0, "opportunities_recovered": 0, "attempts": 0},
        )
        metrics.opportunities += 1
        metrics.revenue_at_risk_minor += at_risk
        bucket["opportunities"] += 1
        bucket["revenue_at_risk_minor"] += at_risk

        probs = dict(truth.action_success_probs)
        recovered = False

        for attempt_number, action in enumerate(
            _actions_for(strategy, scenario, max_attempts), start=1
        ):
            metrics.attempts += 1
            bucket["attempts"] += 1
            if not truth.is_recoverable:
                metrics.unnecessary_attempts += 1
            if action in NOTIFYING_ACTIONS:
                metrics.customer_notifications += 1

            draw = draw_outcome(
                seed=seed,
                opportunity_ref=reproducibility_key(opportunity),
                attempt_number=attempt_number,
                action=action,
                action_success_probs=probs,
            )
            if draw.outcome is Outcome.SUCCESS:
                recovered = True
                break

        if recovered:
            # Exactly as in the real ledger: one opportunity settles once, in full.
            metrics.recovered_revenue_minor += at_risk
            metrics.opportunities_recovered += 1
            bucket["recovered_revenue_minor"] += at_risk
            bucket["opportunities_recovered"] += 1

    for data in per_scenario.values():
        data["recovery_rate"] = round(
            data["recovered_revenue_minor"] / data["revenue_at_risk_minor"], 4
        ) if data["revenue_at_risk_minor"] else 0.0
    metrics.per_scenario = per_scenario
    return metrics


async def measure_recoverai(
    session: AsyncSession,
    run_id: uuid.UUID,
    rows: list[tuple[RecoveryOpportunity, SimulationGroundTruth]],
) -> StrategyMetrics:
    """RecoverAI's own numbers, read from the ledger — never recomputed or estimated."""
    from app.db.models import RecoveryAttempt

    metrics = StrategyMetrics(strategy=str(BaselineStrategy.RECOVERAI))
    per_scenario: dict[str, dict[str, int]] = {}
    truth_by_id = {r[0].id: r[1] for r in rows}

    ledger_by_opp = {
        row.opportunity_id: row
        for row in (
            await session.execute(
                select(RecoveryLedger).where(
                    RecoveryLedger.simulation_run_id == run_id,
                    RecoveryLedger.entry_type == LedgerEntryType.RECOVERED,
                )
            )
        ).scalars()
    }

    attempts = (
        (
            await session.execute(
                select(RecoveryAttempt).where(
                    RecoveryAttempt.opportunity_id.in_([r[0].id for r in rows])
                )
            )
        )
        .scalars()
        .all()
    ) if rows else []

    attempts_by_opp: dict[uuid.UUID, list] = {}
    for attempt in attempts:
        attempts_by_opp.setdefault(attempt.opportunity_id, []).append(attempt)

    for opportunity, _truth in rows:
        scenario = str(opportunity.scenario)
        at_risk = int(opportunity.amount_at_risk_minor)
        bucket = per_scenario.setdefault(
            scenario,
            {"revenue_at_risk_minor": 0, "recovered_revenue_minor": 0,
             "opportunities": 0, "opportunities_recovered": 0, "attempts": 0},
        )
        metrics.opportunities += 1
        metrics.revenue_at_risk_minor += at_risk
        bucket["opportunities"] += 1
        bucket["revenue_at_risk_minor"] += at_risk

        own_attempts = attempts_by_opp.get(opportunity.id, [])
        metrics.attempts += len(own_attempts)
        bucket["attempts"] += len(own_attempts)
        truth = truth_by_id.get(opportunity.id)
        if truth is not None and not truth.is_recoverable:
            metrics.unnecessary_attempts += len(own_attempts)
        metrics.customer_notifications += int(opportunity.notification_count or 0)

        entry = ledger_by_opp.get(opportunity.id)
        if entry is not None:
            metrics.recovered_revenue_minor += int(entry.recovered_amount_minor)
            metrics.opportunities_recovered += 1
            bucket["recovered_revenue_minor"] += int(entry.recovered_amount_minor)
            bucket["opportunities_recovered"] += 1

    for data in per_scenario.values():
        data["recovery_rate"] = round(
            data["recovered_revenue_minor"] / data["revenue_at_risk_minor"], 4
        ) if data["revenue_at_risk_minor"] else 0.0
    metrics.per_scenario = per_scenario
    return metrics


async def compute_baselines(
    session: AsyncSession, run_id: uuid.UUID, *, seed: int, max_attempts: int
) -> dict[str, dict]:
    """Compute and persist every strategy's metrics for a run."""
    pairs = (
        await session.execute(
            select(RecoveryOpportunity, SimulationGroundTruth)
            .join(
                SimulationGroundTruth,
                SimulationGroundTruth.opportunity_id == RecoveryOpportunity.id,
            )
            .where(RecoveryOpportunity.simulation_run_id == run_id)
        )
    ).all()
    rows = [(p[0], p[1]) for p in pairs]

    results: dict[str, dict] = {}
    for strategy in (
        BaselineStrategy.NO_RECOVERY,
        BaselineStrategy.ALWAYS_RETRY,
        BaselineStrategy.FIXED_RETRY,
    ):
        metrics = replay_strategy(strategy, rows, seed=seed, max_attempts=max_attempts)
        results[str(strategy)] = metrics.to_dict()

    recoverai = await measure_recoverai(session, run_id, rows)
    results[str(BaselineStrategy.RECOVERAI)] = recoverai.to_dict()

    for strategy_name, metrics_dict in results.items():
        session.add(
            BaselineResult(
                simulation_run_id=run_id, strategy=strategy_name, metrics=metrics_dict
            )
        )
    await session.flush()

    log.info(
        "baselines.computed",
        run_id=str(run_id),
        **{k: v["recovered_revenue_minor"] for k, v in results.items()},
    )
    return results


def uplift(results: dict[str, dict]) -> dict:
    """RecoverAI's advantage over the strongest naive strategy."""
    ours = results.get(str(BaselineStrategy.RECOVERAI), {})
    rivals = [
        v
        for k, v in results.items()
        if k != str(BaselineStrategy.RECOVERAI) and k != str(BaselineStrategy.NO_RECOVERY)
    ]
    if not ours or not rivals:
        return {}
    best = max(rivals, key=lambda m: m["recovered_revenue_minor"])
    delta = ours["recovered_revenue_minor"] - best["recovered_revenue_minor"]
    return {
        "best_baseline": best["strategy"],
        "best_baseline_recovered_minor": best["recovered_revenue_minor"],
        "recoverai_recovered_minor": ours["recovered_revenue_minor"],
        "uplift_minor": delta,
        "uplift_percent": round(
            (delta / best["recovered_revenue_minor"] * 100), 2
        ) if best["recovered_revenue_minor"] else None,
        # Signed deltas, not "savings". RecoverAI often spends *more* touches than a
        # short static ladder — its advantage is what each touch earns, which is why
        # efficiency is reported alongside rather than a flattering one-sided number.
        "attempt_delta": ours["attempts"] - best["attempts"],
        "unnecessary_attempt_delta": (
            ours["unnecessary_attempts"] - best["unnecessary_attempts"]
        ),
        "notification_delta": (
            ours["customer_notifications"] - best["customer_notifications"]
        ),
        "efficiency_minor": ours["recovery_efficiency_minor"],
        "best_baseline_efficiency_minor": best["recovery_efficiency_minor"],
        "efficiency_uplift_percent": round(
            (ours["recovery_efficiency_minor"] / best["recovery_efficiency_minor"] - 1) * 100, 2
        ) if best["recovery_efficiency_minor"] else None,
    }
