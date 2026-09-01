"""Dashboard endpoints (§27–§31)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import metrics
from app.api import serializers as ser
from app.api.deps import db_session
from app.api.envelope import ok
from app.db.models import AuditLog, Customer, RecoveryOpportunity
from app.domain.enums import Source

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
async def summary(
    run_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(db_session),
) -> dict:
    totals = await metrics.revenue_totals(session, run_id=run_id)
    funnel = await metrics.pipeline_funnel(session, run_id=run_id)
    return ok(
        {
            "totals": ser.totals(totals),
            "funnel": funnel,
            "scenarios": [
                ser.scenario_row(r)
                for r in await metrics.scenario_breakdown(session, run_id=run_id)
            ],
        }
    )


@router.get("/scenarios")
async def scenarios(
    run_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(db_session),
) -> dict:
    rows = await metrics.scenario_breakdown(session, run_id=run_id)
    return ok([ser.scenario_row(r) for r in rows])


@router.get("/pipeline")
async def pipeline(
    run_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(db_session),
) -> dict:
    return ok(await metrics.pipeline_funnel(session, run_id=run_id))


@router.get("/activity")
async def activity(
    limit: int = Query(40, ge=1, le=200),
    run_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Live activity feed (§31) — the audit log, newest first."""
    stmt = select(AuditLog).order_by(desc(AuditLog.occurred_at)).limit(limit)
    if run_id:
        stmt = stmt.where(AuditLog.simulation_run_id == run_id)
    entries = (await session.execute(stmt)).scalars().all()
    return ok([ser.audit_entry(e) for e in entries])


@router.get("/sources")
async def sources(session: AsyncSession = Depends(db_session)) -> dict:
    """Split of opportunities by origin — simulator vs live gateway (§42)."""
    from sqlalchemy import func

    rows = (
        await session.execute(
            select(
                RecoveryOpportunity.source,
                func.count(RecoveryOpportunity.id),
                func.coalesce(func.sum(RecoveryOpportunity.amount_at_risk_minor), 0),
                func.coalesce(func.sum(RecoveryOpportunity.recovered_amount_minor), 0),
            ).group_by(RecoveryOpportunity.source)
        )
    ).all()
    by_source = {
        str(source): {
            "opportunities": int(count),
            "revenue_at_risk": ser.money(int(at_risk)),
            "recovered_revenue": ser.money(int(recovered)),
        }
        for source, count, at_risk, recovered in rows
    }
    for source in Source:
        by_source.setdefault(
            str(source),
            {
                "opportunities": 0,
                "revenue_at_risk": ser.money(0),
                "recovered_revenue": ser.money(0),
            },
        )
    return ok(by_source)


@router.get("/customers/top")
async def top_customers(
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(db_session),
) -> dict:
    from sqlalchemy import func

    rows = (
        await session.execute(
            select(
                Customer,
                func.count(RecoveryOpportunity.id).label("opportunities"),
                func.coalesce(func.sum(RecoveryOpportunity.recovered_amount_minor), 0).label(
                    "recovered"
                ),
            )
            .join(RecoveryOpportunity, RecoveryOpportunity.customer_id == Customer.id)
            .group_by(Customer.id)
            .order_by(desc("recovered"))
            .limit(limit)
        )
    ).all()
    return ok(
        [
            {
                "customer_ref": c.customer_ref,
                "name": c.name,
                "segment": c.segment,
                "opportunities": int(n),
                "recovered_revenue": ser.money(int(recovered)),
                "historical_success_rate": float(c.historical_success_rate or 0),
            }
            for c, n, recovered in rows
        ]
    )
