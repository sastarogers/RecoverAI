"""Simulation runner (§35) — generate a world, then run it through the real pipeline.

Nothing here is a shortcut around the platform: the opportunities it creates go through
the same detector, context builder, agent, policy engine, executor and ledger that a
live Razorpay event does. That is what makes the simulated numbers meaningful.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import RecoveryAgent
from app.core.ids import utcnow
from app.core.logging import get_logger
from app.db.models import (
    Customer,
    RecoveryOpportunity,
    SimulationGroundTruth,
    SimulationRun,
)
from app.domain.enums import Actor, Scenario, SimulationStatus
from app.executor.simulator import SimulatorExecutor
from app.ingestion.detector import detect_opportunity
from app.pipeline.orchestrator import run_until_resolved
from app.policy.rules import PolicyLimits
from app.services import audit, refs
from app.simulation.config import SimulationConfig
from app.simulation.generators import transactions as tx
from app.simulation.generators.customers import generate_customers
from app.simulation.ground_truth import compute_ground_truth
from app.simulation.outcome import reproducibility_key

log = get_logger("recoverai.simulation")

#: How many opportunities to hold in the session at once during the pipeline phase.
OPPORTUNITY_CHUNK_SIZE = 100

ENGINE_VERSION = "1.0.0"
DATA_VERSION = "1.0.0"

ProgressFn = Callable[[dict], Awaitable[None]] | None

STAGES = (
    "Generating customers",
    "Generating transactions",
    "Creating failures",
    "Analyzing opportunities",
    "Executing recovery strategies",
    "Calculating results",
)


@dataclass(slots=True)
class RunSummary:
    run_id: uuid.UUID
    run_ref: str
    counters: dict


async def create_run(session: AsyncSession, config: SimulationConfig) -> SimulationRun:
    run = SimulationRun(
        run_ref=await refs.next_ref(session, "SIM"),
        seed=config.seed,
        config=config.model_dump(mode="json"),
        status=SimulationStatus.PENDING,
        progress={"stage": "queued", "percent": 0, "counters": {}},
        ai_mode=config.ai_mode,
        engine_version=ENGINE_VERSION,
        data_version=DATA_VERSION,
        label=config.label,
        policy_snapshot={
            "max_attempts": config.max_attempts,
            "max_notifications": config.max_notifications,
        },
    )
    session.add(run)
    await session.flush()
    return run


async def execute_run(
    session: AsyncSession,
    run: SimulationRun,
    config: SimulationConfig,
    *,
    on_progress: ProgressFn = None,
) -> RunSummary:
    """Run the full simulation. Caller owns the transaction."""
    started = time.perf_counter()
    run.status = SimulationStatus.RUNNING
    run.started_at = utcnow()
    counters: dict[str, int] = {}

    async def progress(stage_index: int, message: str, **extra) -> None:
        payload = {
            "stage": STAGES[stage_index],
            "stage_index": stage_index,
            "total_stages": len(STAGES),
            "percent": round((stage_index / len(STAGES)) * 100),
            "message": message,
            "counters": {**counters, **extra},
        }
        run.progress = payload
        await session.flush()
        if on_progress:
            await on_progress(payload)

    # ---- 1. customers -------------------------------------------------
    await progress(0, f"Generating {config.num_customers} customers")
    # Reserve a globally unique block of refs so a second run does not collide with the
    # first. Only the display refs shift; the seed still drives identical data.
    customer_offset = (
        await refs.next_number(session, "C", count=config.num_customers)
    ) - 1 if config.num_customers else 0
    customers = generate_customers(config, ref_offset=customer_offset)
    for gc in customers:
        gc.model.simulation_run_id = run.id
        session.add(gc.model)
    await session.flush()
    counters["customers"] = len(customers)

    # ---- 2/3. transactions and failures -------------------------------
    await progress(1, "Generating orders, checkouts and subscriptions")
    batches: list[tx.GeneratedBatch] = []
    if config.wants(Scenario.FAILED_PAYMENT) and config.num_payments:
        offset = (await refs.next_number(session, "P", count=config.num_payments)) - 1
        batches.append(tx.generate_payments(config, customers, ref_offset=offset))
    if config.wants(Scenario.CHECKOUT_ABANDONMENT) and config.num_checkouts:
        offset = (await refs.next_number(session, "CHK", count=config.num_checkouts)) - 1
        batches.append(tx.generate_checkouts(config, customers, ref_offset=offset))
    if config.wants(Scenario.FAILED_SUBSCRIPTION) and config.num_subscriptions:
        offset = (await refs.next_number(session, "SUB", count=config.num_subscriptions)) - 1
        batches.append(tx.generate_subscriptions(config, customers, ref_offset=offset))

    for batch in batches:
        for row in (*batch.orders, *batch.payments, *batch.checkouts, *batch.subscriptions):
            if hasattr(row, "simulation_run_id"):
                row.simulation_run_id = run.id
            session.add(row)
        await session.flush()
        # Subscription events reference subscriptions, so they are added after flush.
        for row in batch.subscription_events:
            session.add(row)
        await session.flush()
        counters.update(batch.stats)

    risk_events = [risk for batch in batches for risk in batch.risk_events]
    await progress(2, f"Created {len(risk_events)} revenue-loss events", **counters)

    # ---- 4. detect opportunities + hidden ground truth -----------------
    await progress(3, f"Detecting opportunities from {len(risk_events)} events")
    opportunities: list[RecoveryOpportunity] = []
    for risk in risk_events:
        detection = await detect_opportunity(session, risk.event, simulation_run_id=run.id)
        if detection.opportunity is None or not detection.created:
            continue
        opportunity = detection.opportunity
        opportunities.append(opportunity)

        truth = compute_ground_truth(
            seed=config.seed,
            opportunity_ref=reproducibility_key(opportunity),
            scenario=Scenario(opportunity.scenario),
            failure_category=opportunity.failure_category,
            amount_minor=int(opportunity.amount_at_risk_minor),
            customer_reliability=risk.reliability,
            price_sensitivity=risk.price_sensitivity,
            unrecoverable_rate=config.unrecoverable_rate,
            abandonment_reason=risk.abandonment_reason,
            hours_since_event=risk.hours_since_event,
        )
        session.add(
            SimulationGroundTruth(
                simulation_run_id=run.id,
                opportunity_id=opportunity.id,
                action_success_probs=truth.action_success_probs,
                latent_factors=truth.latent_factors,
                optimal_action=truth.optimal_action,
                optimal_probability=truth.optimal_probability,
                is_recoverable=truth.is_recoverable,
            )
        )
    await session.flush()
    counters["opportunities"] = len(opportunities)
    counters["revenue_at_risk_minor"] = sum(
        int(o.amount_at_risk_minor) for o in opportunities
    )

    # ---- 5. run the real pipeline --------------------------------------
    await progress(4, f"Running RecoverAI over {len(opportunities)} opportunities", **counters)
    agent = RecoveryAgent(mode=config.ai_mode)
    limits = PolicyLimits(
        max_attempts=config.max_attempts, max_notifications=config.max_notifications
    )

    recovered = attempts = blocked = stopped = 0
    opportunity_ids = [o.id for o in opportunities]
    # Commit the generated world before the pipeline runs, so the identity map that the
    # pipeline works against starts empty.
    await session.commit()

    # Process in chunks. A single session holding every customer, payment, decision and
    # attempt makes each flush re-scan the whole identity map, and the run degrades
    # quadratically; committing and clearing per chunk keeps flushes flat.
    index = 0
    for start in range(0, len(opportunity_ids), OPPORTUNITY_CHUNK_SIZE):
        chunk_ids = opportunity_ids[start : start + OPPORTUNITY_CHUNK_SIZE]
        chunk = (
            (
                await session.execute(
                    select(RecoveryOpportunity).where(RecoveryOpportunity.id.in_(chunk_ids))
                )
            )
            .scalars()
            .all()
        )
        # One query each for the customers and the hidden truths this chunk needs,
        # instead of one per opportunity per attempt.
        customers_by_id = {
            c.id: c
            for c in (
                await session.execute(
                    select(Customer).where(
                        Customer.id.in_({o.customer_id for o in chunk})
                    )
                )
            ).scalars()
        }
        truths_by_opportunity = {
            t.opportunity_id: t
            for t in (
                await session.execute(
                    select(SimulationGroundTruth).where(
                        SimulationGroundTruth.opportunity_id.in_(chunk_ids)
                    )
                )
            ).scalars()
        }
        executor = SimulatorExecutor(
            session, seed=config.seed, ground_truth=truths_by_opportunity
        )

        for opportunity in chunk:
            cycles = await run_until_resolved(
                session,
                opportunity,
                agent=agent,
                executor=executor,
                limits=limits,
                customer=customers_by_id.get(opportunity.customer_id),
            )
            attempts += sum(1 for c in cycles if c.ran)
            blocked += sum(1 for c in cycles if c.policy_verdict == "BLOCKED")
            stopped += sum(1 for c in cycles if c.action == "STOP")
            recovered += sum(c.recovered_amount_minor for c in cycles if c.settled)
            index += 1

        await session.commit()
        session.expunge_all()

        # Progress lives on the run row, which the chunk commit just cleared from the
        # session, so it is re-read before being updated.
        run = (
            await session.execute(select(SimulationRun).where(SimulationRun.id == run.id))
        ).scalar_one()
        await progress(
            4,
            f"Processed {index} of {len(opportunity_ids)} opportunities",
            **counters,
            processed=index,
            recovered_minor=recovered,
        )
        await session.commit()
        # Yield to the event loop so SSE progress actually streams.
        await asyncio.sleep(0)

    counters.update(
        {
            "recovery_attempts": attempts,
            "blocked_actions": blocked,
            "stop_decisions": stopped,
            "recovered_revenue_minor": recovered,
            **{f"ai_{k}": v for k, v in _flatten(agent.stats()).items()},
        }
    )
    await agent.aclose()

    # ---- 6. results ----------------------------------------------------
    await progress(5, "Calculating metrics and baselines", **counters)
    baseline_results: dict = {}
    if config.compute_baselines:
        from app.simulation.baselines import compute_baselines, uplift

        baseline_results = await compute_baselines(
            session, run.id, seed=config.seed, max_attempts=config.max_attempts
        )
        counters["uplift_minor"] = uplift(baseline_results).get("uplift_minor", 0)
    run.ai_model = getattr(agent, "_client", None) and getattr(agent._client, "model", None)
    run.ai_mode = agent.effective_mode
    run.status = SimulationStatus.COMPLETED
    run.completed_at = utcnow()
    run.duration_ms = int((time.perf_counter() - started) * 1000)
    run.results = {
        "counters": counters,
        "baselines": baseline_results,
        "uplift": (
            __import__("app.simulation.baselines", fromlist=["uplift"]).uplift(baseline_results)
            if baseline_results
            else {}
        ),
        "ai": agent.stats(),
    }
    run.progress = {
        "stage": "Complete",
        "stage_index": len(STAGES),
        "total_stages": len(STAGES),
        "percent": 100,
        "message": "Simulation complete",
        "counters": counters,
    }

    await audit.record(
        session,
        entity_type="simulation_run",
        entity_id=run.run_ref,
        actor=Actor.SIMULATOR,
        action="SIMULATION_COMPLETED",
        detail={"seed": config.seed, "counters": counters},
        simulation_run_id=run.id,
    )
    log.info("simulation.completed", run_ref=run.run_ref, **{k: v for k, v in counters.items() if isinstance(v, int)})

    return RunSummary(run.id, run.run_ref, counters)


def _flatten(stats: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, value in stats.items():
        if isinstance(value, dict):
            out.update({f"{key}_{k}": v for k, v in value.items() if isinstance(v, int)})
        elif isinstance(value, int):
            out[key] = value
    return out


async def opportunities_for_run(
    session: AsyncSession, run_id: uuid.UUID
) -> list[RecoveryOpportunity]:
    return list(
        (
            await session.execute(
                select(RecoveryOpportunity).where(
                    RecoveryOpportunity.simulation_run_id == run_id
                )
            )
        )
        .scalars()
        .all()
    )
