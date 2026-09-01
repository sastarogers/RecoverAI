"""Analytics endpoints: baselines, calibration, AI and business metrics (§36–§38)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.analytics import metrics
from app.api import serializers as ser
from app.api.deps import db_session
from app.api.envelope import ok, paginated
from app.db.models import BaselineResult, RecoveryAttempt, RecoveryOpportunity, SimulationRun
from app.simulation.baselines import uplift

router = APIRouter(tags=["analytics"])


@router.get("/recovery/metrics")
async def recovery_metrics(
    run_id: uuid.UUID | None = None, session: AsyncSession = Depends(db_session)
) -> dict:
    totals = await metrics.revenue_totals(session, run_id=run_id)
    funnel = await metrics.pipeline_funnel(session, run_id=run_id)
    ai = await metrics.ai_performance(session, run_id=run_id)

    attempts = funnel["recovery_attempts"] or 0
    recovered = totals.recovered_revenue_minor
    return ok(
        {
            "business": {
                **ser.totals(totals),
                "revenue_recovered_per_attempt": ser.money(
                    int(recovered / attempts) if attempts else 0
                ),
                "average_attempts_per_recovery": (
                    round(attempts / totals.opportunities_recovered, 3)
                    if totals.opportunities_recovered
                    else 0.0
                ),
                "recovery_efficiency": (
                    round(funnel["successful_recoveries"] / attempts, 4) if attempts else 0.0
                ),
            },
            "ai": ai,
            "funnel": funnel,
            "policy": {
                "blocked_actions": funnel["policy_blocked"],
                "approved_actions": funnel["policy_approved"],
                "block_rate": (
                    round(
                        funnel["policy_blocked"]
                        / (funnel["policy_blocked"] + funnel["policy_approved"]),
                        4,
                    )
                    if (funnel["policy_blocked"] + funnel["policy_approved"])
                    else 0.0
                ),
            },
        }
    )


@router.get("/recovery/attempts")
async def list_attempts(
    run_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(db_session),
) -> dict:
    from sqlalchemy import func

    stmt = (
        select(RecoveryAttempt, RecoveryOpportunity)
        .options(selectinload(RecoveryAttempt.outcome))
        .join(RecoveryOpportunity, RecoveryOpportunity.id == RecoveryAttempt.opportunity_id)
    )
    count_stmt = select(func.count(RecoveryAttempt.id)).join(
        RecoveryOpportunity, RecoveryOpportunity.id == RecoveryAttempt.opportunity_id
    )
    if run_id:
        stmt = stmt.where(RecoveryOpportunity.simulation_run_id == run_id)
        count_stmt = count_stmt.where(RecoveryOpportunity.simulation_run_id == run_id)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(desc(RecoveryAttempt.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return paginated(
        [
            {
                **ser.attempt(attempt, attempt.outcome),
                "opportunity_ref": opportunity.opportunity_ref,
                "scenario": opportunity.scenario,
                "amount_at_risk": ser.money(opportunity.amount_at_risk_minor),
            }
            for attempt, opportunity in rows
        ],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get("/analytics/baselines")
async def baselines(
    run_id: uuid.UUID | None = None, session: AsyncSession = Depends(db_session)
) -> dict:
    """Baseline comparison (§36). Defaults to the most recent completed run."""
    if run_id is None:
        run_id = (
            await session.execute(
                select(SimulationRun.id)
                .where(SimulationRun.status == "COMPLETED")
                .order_by(desc(SimulationRun.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()
    if run_id is None:
        return ok({"run_id": None, "strategies": [], "uplift": {}})

    rows = {
        row.strategy: row.metrics
        for row in (
            await session.execute(
                select(BaselineResult).where(BaselineResult.simulation_run_id == run_id)
            )
        ).scalars()
    }
    order = ["NO_RECOVERY", "ALWAYS_RETRY", "FIXED_RETRY", "RECOVERAI"]
    return ok(
        {
            "run_id": str(run_id),
            "strategies": [ser.baseline_row(rows[k]) for k in order if k in rows],
            "uplift": uplift(rows),
        }
    )


@router.get("/analytics/calibration")
async def calibration(
    run_id: uuid.UUID | None = None, session: AsyncSession = Depends(db_session)
) -> dict:
    """Predicted probability vs realized outcome (§37)."""
    curve = await metrics.calibration_curve(session, run_id=run_id)
    ai = await metrics.ai_performance(session, run_id=run_id)
    return ok(
        {
            "curve": curve,
            "expected_calibration_error": ai["calibration_error"],
            "brier_score": ai["brier_score"],
            "average_predicted_probability": ai["average_predicted_probability"],
            "actual_recovery_rate": ai["actual_recovery_rate"],
        }
    )
