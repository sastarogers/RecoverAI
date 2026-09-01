"""§57 — the same seed must reproduce the same experiment.

This is easy to break in ways nothing else catches: allocating a globally-unique display
ref and then seeding randomness on it makes a run depend on how many runs preceded it.
These tests run a full simulation twice in one database, which is exactly the situation
that exposes it.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import RecoveryLedger, RecoveryOpportunity, SimulationGroundTruth
from app.simulation.config import SimulationConfig
from app.simulation.runner import create_run, execute_run

BASE = dict(
    num_customers=40, num_payments=60, num_checkouts=40, num_subscriptions=25,
    ai_mode="heuristic", compute_baselines=False,
)


async def _run(session, seed: int) -> dict:
    config = SimulationConfig(seed=seed, **BASE)
    run = await create_run(session, config)
    summary = await execute_run(session, run, config)
    return {"run_id": run.id, "counters": summary.counters}


COMPARED = (
    "opportunities",
    "revenue_at_risk_minor",
    "recovered_revenue_minor",
    "recovery_attempts",
    "blocked_actions",
    "stop_decisions",
    "payments_failed",
    "checkouts_abandoned",
    "renewals_failed",
)


async def test_same_seed_reproduces_identical_results(session):
    first = await _run(session, 20260901)
    second = await _run(session, 20260901)

    for key in COMPARED:
        assert first["counters"].get(key) == second["counters"].get(key), (
            f"{key} differed between two runs of the same seed: "
            f"{first['counters'].get(key)} vs {second['counters'].get(key)}"
        )


async def test_different_seeds_produce_different_results(session):
    first = await _run(session, 111)
    second = await _run(session, 222)
    assert (
        first["counters"]["recovered_revenue_minor"]
        != second["counters"]["recovered_revenue_minor"]
    )


async def test_second_run_does_not_reuse_refs(session):
    """Refs must stay globally unique even though the data repeats."""
    await _run(session, 555)
    await _run(session, 555)

    total, distinct = (
        await session.execute(
            select(
                func.count(RecoveryOpportunity.id),
                func.count(func.distinct(RecoveryOpportunity.opportunity_ref)),
            )
        )
    ).one()
    assert total == distinct, "opportunity refs collided across runs"


async def test_reproducibility_key_is_independent_of_display_ref(session):
    """The same logical opportunity carries the same simulation key in both runs."""
    first = await _run(session, 777)
    second = await _run(session, 777)

    async def keys(run_id):
        rows = (
            await session.execute(
                select(RecoveryOpportunity.simulation_key)
                .where(RecoveryOpportunity.simulation_run_id == run_id)
                .order_by(RecoveryOpportunity.simulation_key)
            )
        ).scalars().all()
        return [r for r in rows]

    assert await keys(first["run_id"]) == await keys(second["run_id"])
    assert all(k for k in await keys(first["run_id"])), "every generated opportunity needs a key"


async def test_ground_truth_is_identical_across_runs_of_a_seed(session):
    """Hidden truth is part of the experiment and must reproduce too."""
    first = await _run(session, 888)
    second = await _run(session, 888)

    async def truths(run_id):
        rows = (
            await session.execute(
                select(
                    RecoveryOpportunity.simulation_key,
                    SimulationGroundTruth.optimal_action,
                    SimulationGroundTruth.optimal_probability,
                    SimulationGroundTruth.is_recoverable,
                )
                .join(
                    SimulationGroundTruth,
                    SimulationGroundTruth.opportunity_id == RecoveryOpportunity.id,
                )
                .where(RecoveryOpportunity.simulation_run_id == run_id)
                .order_by(RecoveryOpportunity.simulation_key)
            )
        ).all()
        return [(k, a, float(p), r) for k, a, p, r in rows]

    assert await truths(first["run_id"]) == await truths(second["run_id"])


async def test_ledger_totals_match_across_runs_of_a_seed(session):
    """The number that matters most — recovered revenue — must reproduce exactly."""
    first = await _run(session, 999)
    second = await _run(session, 999)

    async def total(run_id):
        return int(
            (
                await session.execute(
                    select(func.coalesce(func.sum(RecoveryLedger.recovered_amount_minor), 0))
                    .where(RecoveryLedger.simulation_run_id == run_id)
                )
            ).scalar_one()
        )

    assert await total(first["run_id"]) == await total(second["run_id"])
