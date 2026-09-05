"""Opportunity list and full-lifecycle detail (§32/§45/§54/§59)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.agent import RecoveryAgent
from app.api import serializers as ser
from app.api.deps import db_session
from app.api.envelope import ok, paginated
from app.api.routes.notifications import serialize_message
from app.core.errors import NotFoundError
from app.db.models import (
    AIDecision,
    AuditLog,
    Customer,
    NotificationMessage,
    PolicyDecision,
    RecoveryAttempt,
    RecoveryLedger,
    RecoveryOpportunity,
    SimulationRun,
)
from app.domain.enums import OpportunityStatus, Scenario, Source
from app.executor.simulator import SimulatorExecutor
from app.pipeline.orchestrator import run_cycle
from app.policy.rules import PolicyLimits

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("")
async def list_opportunities(
    scenario: Scenario | None = None,
    status: OpportunityStatus | None = None,
    source: Source | None = None,
    run_id: uuid.UUID | None = None,
    q: str | None = Query(None, description="Search by opportunity or customer ref"),
    recovered_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    session: AsyncSession = Depends(db_session),
) -> dict:
    stmt = select(RecoveryOpportunity, Customer).join(
        Customer, Customer.id == RecoveryOpportunity.customer_id
    )
    count_stmt = select(func.count(RecoveryOpportunity.id)).join(
        Customer, Customer.id == RecoveryOpportunity.customer_id
    )

    filters = []
    if scenario:
        filters.append(RecoveryOpportunity.scenario == scenario)
    if status:
        filters.append(RecoveryOpportunity.status == status)
    if source:
        filters.append(RecoveryOpportunity.source == source)
    if run_id:
        filters.append(RecoveryOpportunity.simulation_run_id == run_id)
    if recovered_only:
        filters.append(RecoveryOpportunity.status == OpportunityStatus.RECOVERED)
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                RecoveryOpportunity.opportunity_ref.ilike(pattern),
                Customer.customer_ref.ilike(pattern),
                Customer.name.ilike(pattern),
            )
        )
    for condition in filters:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(desc(RecoveryOpportunity.detected_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return paginated(
        [ser.opportunity_row(opp, customer) for opp, customer in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get("/{opportunity_id}")
async def get_opportunity(
    opportunity_id: str, session: AsyncSession = Depends(db_session)
) -> dict:
    """Full lifecycle: context, decisions, policy trace, attempts, outcomes, ledger."""
    opportunity = await _resolve(session, opportunity_id)

    customer = (
        await session.execute(select(Customer).where(Customer.id == opportunity.customer_id))
    ).scalar_one()

    decisions = (
        (
            await session.execute(
                select(AIDecision)
                .where(AIDecision.opportunity_id == opportunity.id)
                .order_by(AIDecision.sequence)
            )
        )
        .scalars()
        .all()
    )
    policies = (
        (
            await session.execute(
                select(PolicyDecision)
                .where(PolicyDecision.opportunity_id == opportunity.id)
                .order_by(PolicyDecision.created_at)
            )
        )
        .scalars()
        .all()
    )
    attempts = (
        (
            await session.execute(
                select(RecoveryAttempt)
                .options(selectinload(RecoveryAttempt.outcome))
                .where(RecoveryAttempt.opportunity_id == opportunity.id)
                .order_by(RecoveryAttempt.attempt_number)
            )
        )
        .scalars()
        .all()
    )
    ledger = (
        (
            await session.execute(
                select(RecoveryLedger).where(RecoveryLedger.opportunity_id == opportunity.id)
            )
        )
        .scalars()
        .all()
    )
    messages = (
        (
            await session.execute(
                select(NotificationMessage)
                .where(NotificationMessage.opportunity_id == opportunity.id)
                .order_by(NotificationMessage.created_at)
            )
        )
        .scalars()
        .all()
    )
    audit = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.entity_id.in_([opportunity.opportunity_ref, *[a.attempt_ref for a in attempts]]))
                .order_by(AuditLog.occurred_at)
            )
        )
        .scalars()
        .all()
    )

    return ok(
        {
            **ser.opportunity_row(opportunity, customer),
            "customer": {
                "customer_ref": customer.customer_ref,
                "name": customer.name,
                "segment": customer.segment,
                "account_age_days": customer.account_age_days,
                "historical_success_rate": float(customer.historical_success_rate or 0),
                "previous_transaction_count": customer.previous_transaction_count,
                "average_order_value": ser.money(customer.average_order_value_minor),
                "lifetime_value": ser.money(customer.lifetime_value_minor),
                "preferred_payment_method": customer.preferred_payment_method,
                "previous_recoveries": customer.previous_recoveries,
            },
            "context_snapshot": opportunity.context_snapshot,
            "ai_decisions": [ser.ai_decision(d) for d in decisions],
            "policy_decisions": [ser.policy_decision(p) for p in policies],
            "attempts": [ser.attempt(a, a.outcome) for a in attempts],
            "ledger": [ser.ledger_entry(e) for e in ledger],
            "messages": [serialize_message(m) for m in messages],
            "timeline": _timeline(opportunity, audit),
        }
    )


@router.post("/{opportunity_id}/recover")
async def recover_now(
    opportunity_id: str, session: AsyncSession = Depends(db_session)
) -> dict:
    """Run one more pipeline cycle manually (§51)."""
    opportunity = await _resolve(session, opportunity_id)

    seed = 0
    if opportunity.simulation_run_id:
        run = (
            await session.execute(
                select(SimulationRun).where(SimulationRun.id == opportunity.simulation_run_id)
            )
        ).scalar_one_or_none()
        seed = int(run.seed) if run else 0

    agent = RecoveryAgent()
    try:
        result = await run_cycle(
            session,
            opportunity,
            agent=agent,
            executor=SimulatorExecutor(session, seed=seed),
            limits=PolicyLimits(),
        )
    finally:
        await agent.aclose()
    await session.commit()

    return ok(
        {
            "opportunity_ref": result.opportunity_ref,
            "ran": result.ran,
            "action": result.action,
            "decision_source": result.decision_source,
            "policy_verdict": result.policy_verdict,
            "blocked_by_rule": result.blocked_by_rule,
            "outcome": result.outcome,
            "recovered_amount": ser.money(result.recovered_amount_minor),
            "settled": result.settled,
            "status": result.status,
            "reason": result.reason,
        }
    )


async def _resolve(session: AsyncSession, identifier: str) -> RecoveryOpportunity:
    """Accept either a UUID or the human ref (OPP0001)."""
    stmt = select(RecoveryOpportunity)
    try:
        stmt = stmt.where(RecoveryOpportunity.id == uuid.UUID(identifier))
    except ValueError:
        stmt = stmt.where(RecoveryOpportunity.opportunity_ref == identifier)
    opportunity = (await session.execute(stmt)).scalar_one_or_none()
    if opportunity is None:
        raise NotFoundError(f"Opportunity {identifier} not found")
    return opportunity


def _timeline(opportunity, audit_entries) -> list[dict]:
    """Event timeline for the detail view (§45)."""
    # Detection is already in the audit log; seeding it here too would double it.
    events: list[dict] = []
    events.extend(
        {
            "at": entry.occurred_at.isoformat(),
            "actor": entry.actor,
            "title": _humanize(entry.action),
            "detail": entry.detail,
        }
        for entry in audit_entries
    )
    return sorted(events, key=lambda e: e["at"])


_TITLES = {
    "OPPORTUNITY_DETECTED": "Revenue opportunity detected",
    "AI_RECOMMENDED": "AI recommended an action",
    "POLICY_APPROVED": "Policy approved",
    "POLICY_BLOCKED": "Policy blocked",
    "RECOVERY_EXECUTED": "Recovery action executed",
    "RECOVERY_SETTLED": "Revenue recovered",
}


def _humanize(action: str) -> str:
    return _TITLES.get(action, action.replace("_", " ").title())
