"""Business and AI performance metrics (§37/§38/§39).

The distinction §39 insists on is enforced structurally here: `recovered_revenue_minor`
is only ever read from the ledger, while `expected_recovery_value_minor` is only ever
computed from AI probabilities. They are separate fields with separate names and are
never summed into one another.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AIDecision,
    PolicyDecision,
    RecoveryAttempt,
    RecoveryLedger,
    RecoveryOpportunity,
    RecoveryOutcome,
    SimulationGroundTruth,
)
from app.domain.enums import (
    LedgerEntryType,
    OpportunityStatus,
    Outcome,
    PolicyVerdict,
    RecoveryAction,
    Scenario,
)


def _scope(stmt, model, run_id: uuid.UUID | None):
    return stmt.where(model.simulation_run_id == run_id) if run_id else stmt


@dataclass(slots=True)
class RevenueTotals:
    revenue_at_risk_minor: int
    recovered_revenue_minor: int
    projected_retention_minor: int
    expected_recovery_value_minor: int
    opportunities: int
    opportunities_recovered: int

    @property
    def recovery_rate(self) -> float:
        return (
            self.recovered_revenue_minor / self.revenue_at_risk_minor
            if self.revenue_at_risk_minor
            else 0.0
        )


async def revenue_totals(
    session: AsyncSession, *, run_id: uuid.UUID | None = None
) -> RevenueTotals:
    """Headline KPIs (§27). Recovered revenue comes only from the ledger."""
    at_risk, opportunities, projected = (
        await session.execute(
            _scope(
                select(
                    func.coalesce(func.sum(RecoveryOpportunity.amount_at_risk_minor), 0),
                    func.count(RecoveryOpportunity.id),
                    func.coalesce(func.sum(RecoveryOpportunity.projected_retention_minor), 0),
                ),
                RecoveryOpportunity,
                run_id,
            )
        )
    ).one()

    recovered, recovered_count = (
        await session.execute(
            _scope(
                select(
                    func.coalesce(func.sum(RecoveryLedger.recovered_amount_minor), 0),
                    func.count(RecoveryLedger.id),
                ).where(RecoveryLedger.entry_type == LedgerEntryType.RECOVERED),
                RecoveryLedger,
                run_id,
            )
        )
    ).one()

    # Expected value is an AI artifact, reported separately and never treated as money.
    expected = (
        await session.execute(
            _scope(
                select(
                    func.coalesce(
                        func.sum(
                            cast(AIDecision.recovery_probability, Numeric)
                            * RecoveryOpportunity.amount_at_risk_minor
                        ),
                        0,
                    )
                )
                .select_from(AIDecision)
                .join(RecoveryOpportunity, RecoveryOpportunity.id == AIDecision.opportunity_id)
                .where(AIDecision.sequence == 1),
                RecoveryOpportunity,
                run_id,
            )
        )
    ).scalar_one()

    return RevenueTotals(
        revenue_at_risk_minor=int(at_risk),
        recovered_revenue_minor=int(recovered),
        projected_retention_minor=int(projected),
        expected_recovery_value_minor=int(expected or 0),
        opportunities=int(opportunities),
        opportunities_recovered=int(recovered_count),
    )


async def scenario_breakdown(
    session: AsyncSession, *, run_id: uuid.UUID | None = None
) -> list[dict]:
    """Per-scenario revenue at risk / recovered / rate (§28)."""
    at_risk_rows = (
        await session.execute(
            _scope(
                select(
                    RecoveryOpportunity.scenario,
                    func.coalesce(func.sum(RecoveryOpportunity.amount_at_risk_minor), 0),
                    func.count(RecoveryOpportunity.id),
                    func.coalesce(func.sum(RecoveryOpportunity.projected_retention_minor), 0),
                ).group_by(RecoveryOpportunity.scenario),
                RecoveryOpportunity,
                run_id,
            )
        )
    ).all()

    recovered_rows = dict(
        
            (row[0], (int(row[1]), int(row[2])))
            for row in (
                await session.execute(
                    _scope(
                        select(
                            RecoveryLedger.scenario,
                            func.coalesce(func.sum(RecoveryLedger.recovered_amount_minor), 0),
                            func.count(RecoveryLedger.id),
                        )
                        .where(RecoveryLedger.entry_type == LedgerEntryType.RECOVERED)
                        .group_by(RecoveryLedger.scenario),
                        RecoveryLedger,
                        run_id,
                    )
                )
            ).all()
        
    )

    out = []
    for scenario in Scenario:
        row = next((r for r in at_risk_rows if r[0] == scenario), None)
        at_risk = int(row[1]) if row else 0
        count = int(row[2]) if row else 0
        projected = int(row[3]) if row else 0
        recovered, recovered_count = recovered_rows.get(str(scenario), (0, 0))
        out.append(
            {
                "scenario": str(scenario),
                "revenue_at_risk_minor": at_risk,
                "recovered_revenue_minor": recovered,
                "recovery_rate": round(recovered / at_risk, 4) if at_risk else 0.0,
                "opportunities": count,
                "opportunities_recovered": recovered_count,
                "projected_retention_minor": projected,
            }
        )
    return out


async def pipeline_funnel(
    session: AsyncSession, *, run_id: uuid.UUID | None = None
) -> dict:
    """Counts for the pipeline visualization (§30)."""
    statuses = dict(
        (row[0], int(row[1]))
        for row in (
            await session.execute(
                _scope(
                    select(RecoveryOpportunity.status, func.count(RecoveryOpportunity.id))
                    .group_by(RecoveryOpportunity.status),
                    RecoveryOpportunity,
                    run_id,
                )
            )
        ).all()
    )

    decisions = (
        await session.execute(
            _scope(
                select(func.count(AIDecision.id))
                .select_from(AIDecision)
                .join(RecoveryOpportunity, RecoveryOpportunity.id == AIDecision.opportunity_id),
                RecoveryOpportunity,
                run_id,
            )
        )
    ).scalar_one()

    approved, blocked = (
        await session.execute(
            _scope(
                select(
                    func.count(case((PolicyDecision.verdict == PolicyVerdict.APPROVED, 1))),
                    func.count(case((PolicyDecision.verdict == PolicyVerdict.BLOCKED, 1))),
                )
                .select_from(PolicyDecision)
                .join(
                    RecoveryOpportunity,
                    RecoveryOpportunity.id == PolicyDecision.opportunity_id,
                ),
                RecoveryOpportunity,
                run_id,
            )
        )
    ).one()

    attempts, successes, failures = (
        await session.execute(
            _scope(
                select(
                    func.count(RecoveryAttempt.id),
                    func.count(case((RecoveryOutcome.outcome == Outcome.SUCCESS, 1))),
                    func.count(case((RecoveryOutcome.outcome == Outcome.FAILURE, 1))),
                )
                .select_from(RecoveryAttempt)
                .join(
                    RecoveryOpportunity,
                    RecoveryOpportunity.id == RecoveryAttempt.opportunity_id,
                )
                .outerjoin(RecoveryOutcome, RecoveryOutcome.attempt_id == RecoveryAttempt.id),
                RecoveryOpportunity,
                run_id,
            )
        )
    ).one()

    return {
        "opportunities_detected": sum(statuses.values()),
        "ai_decisions": int(decisions),
        "policy_approved": int(approved),
        "policy_blocked": int(blocked),
        "recovery_attempts": int(attempts),
        "successful_recoveries": int(successes),
        "failed_attempts": int(failures),
        "recovered": statuses.get(str(OpportunityStatus.RECOVERED), 0),
        "by_status": statuses,
    }


async def ai_performance(
    session: AsyncSession, *, run_id: uuid.UUID | None = None
) -> dict:
    """AI decision quality (§37).

    `action_accuracy` compares the chosen action against the simulator's hindsight
    optimum. It is computed *after the fact* and never fed back to the agent.
    """
    rows = (
        await session.execute(
            _scope(
                select(
                    AIDecision.action,
                    AIDecision.recovery_probability,
                    AIDecision.confidence,
                    AIDecision.decision_source,
                    SimulationGroundTruth.optimal_action,
                    SimulationGroundTruth.is_recoverable,
                    RecoveryOutcome.outcome,
                )
                .select_from(AIDecision)
                .join(RecoveryOpportunity, RecoveryOpportunity.id == AIDecision.opportunity_id)
                .outerjoin(
                    SimulationGroundTruth,
                    SimulationGroundTruth.opportunity_id == AIDecision.opportunity_id,
                )
                .outerjoin(
                    RecoveryAttempt,
                    (RecoveryAttempt.opportunity_id == AIDecision.opportunity_id)
                    & (RecoveryAttempt.ai_decision_id == AIDecision.id),
                )
                .outerjoin(RecoveryOutcome, RecoveryOutcome.attempt_id == RecoveryAttempt.id),
                RecoveryOpportunity,
                run_id,
            )
        )
    ).all()

    if not rows:
        return {
            "decisions": 0, "action_accuracy": None, "average_predicted_probability": None,
            "actual_recovery_rate": None, "calibration_error": None,
            "unnecessary_action_rate": None, "decision_sources": {},
        }

    total = len(rows)
    with_truth = [r for r in rows if r[4] is not None]
    optimal_hits = sum(1 for r in with_truth if str(r[0]) == str(r[4]))
    predicted = [float(r[1]) for r in rows]
    resolved = [r for r in rows if r[6] is not None]
    successes = sum(1 for r in resolved if r[6] == Outcome.SUCCESS)

    # Actions spent on opportunities that were never recoverable.
    actionable = [r for r in rows if r[5] is not None and str(r[0]) != str(RecoveryAction.STOP)]
    unnecessary = sum(1 for r in actionable if r[5] is False)

    sources: dict[str, int] = {}
    for row in rows:
        sources[str(row[3])] = sources.get(str(row[3]), 0) + 1

    avg_predicted = sum(predicted) / total
    actual_rate = successes / len(resolved) if resolved else 0.0

    # Expected Calibration Error: bucket the predictions, then take the sample-weighted
    # gap between what was predicted and what actually happened.
    #
    # The naive alternative, mean |p - outcome|, is not a calibration measure at all —
    # against binary outcomes it is minimised by predicting 0 or 1, so a perfectly
    # calibrated 25% forecaster still "scores" 0.375. ECE is ~0 when predictions match
    # observed frequencies, which is the property being claimed.
    resolved_pairs = [
        (float(r[1]), 1.0 if r[6] == Outcome.SUCCESS else 0.0) for r in resolved
    ]
    calibration_error = _expected_calibration_error(resolved_pairs)
    brier = (
        sum((p - o) ** 2 for p, o in resolved_pairs) / len(resolved_pairs)
        if resolved_pairs
        else None
    )

    return {
        "decisions": total,
        "action_accuracy": round(optimal_hits / len(with_truth), 4) if with_truth else None,
        "average_predicted_probability": round(avg_predicted, 4),
        "actual_recovery_rate": round(actual_rate, 4),
        "calibration_error": round(calibration_error, 4) if calibration_error is not None else None,
        "brier_score": round(brier, 4) if brier is not None else None,
        "unnecessary_action_rate": (
            round(unnecessary / len(actionable), 4) if actionable else None
        ),
        "decision_sources": sources,
        "stop_decisions": sum(1 for r in rows if str(r[0]) == str(RecoveryAction.STOP)),
    }


def _expected_calibration_error(
    pairs: list[tuple[float, float]], buckets: int = 10
) -> float | None:
    """Sample-weighted mean gap between predicted probability and realized frequency."""
    if not pairs:
        return None
    total = len(pairs)
    error = 0.0
    for i in range(buckets):
        low, high = i / buckets, (i + 1) / buckets
        members = [
            (p, o)
            for p, o in pairs
            if (low <= p < high) or (i == buckets - 1 and p == 1.0)
        ]
        if not members:
            continue
        mean_predicted = sum(p for p, _ in members) / len(members)
        observed = sum(o for _, o in members) / len(members)
        error += (len(members) / total) * abs(mean_predicted - observed)
    return error


async def calibration_curve(
    session: AsyncSession, *, run_id: uuid.UUID | None = None, buckets: int = 10
) -> list[dict]:
    """Predicted probability vs realized success rate, bucketed."""
    rows = (
        await session.execute(
            _scope(
                select(AIDecision.recovery_probability, RecoveryOutcome.outcome)
                .select_from(AIDecision)
                .join(RecoveryOpportunity, RecoveryOpportunity.id == AIDecision.opportunity_id)
                .join(
                    RecoveryAttempt,
                    (RecoveryAttempt.opportunity_id == AIDecision.opportunity_id)
                    & (RecoveryAttempt.ai_decision_id == AIDecision.id),
                )
                .join(RecoveryOutcome, RecoveryOutcome.attempt_id == RecoveryAttempt.id),
                RecoveryOpportunity,
                run_id,
            )
        )
    ).all()

    out = []
    for i in range(buckets):
        low, high = i / buckets, (i + 1) / buckets
        in_bucket = [r for r in rows if low <= float(r[0]) < high or (i == buckets - 1 and float(r[0]) == 1.0)]
        if not in_bucket:
            out.append({"bucket": f"{low:.0%}-{high:.0%}", "predicted": round((low + high) / 2, 3),
                        "actual": None, "count": 0})
            continue
        successes = sum(1 for r in in_bucket if r[1] == Outcome.SUCCESS)
        out.append(
            {
                "bucket": f"{low:.0%}-{high:.0%}",
                "predicted": round(sum(float(r[0]) for r in in_bucket) / len(in_bucket), 4),
                "actual": round(successes / len(in_bucket), 4),
                "count": len(in_bucket),
            }
        )
    return out
