"""Simulation Control Centre endpoints (§34/§35/§56/§57)."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.analytics import metrics
from app.api import serializers as ser
from app.api.deps import db_session
from app.api.envelope import ok
from app.core.errors import NotFoundError
from app.db.models import BaselineResult, SimulationRun
from app.db.session import SessionLocal
from app.domain.enums import SimulationStatus
from app.services import simulation_service
from app.simulation.baselines import uplift
from app.simulation.config import (
    DEFAULT_ABANDONMENT_REASONS,
    DEFAULT_FAILURE_DISTRIBUTION,
    DEFAULT_METHOD_DISTRIBUTION,
    DEFAULT_SEGMENT_DISTRIBUTION,
    PRESETS,
    SimulationConfig,
)

router = APIRouter(prefix="/simulation", tags=["simulation"])


@router.get("/defaults")
async def defaults() -> dict:
    """Everything the control centre needs to render its form."""
    return ok(
        {
            "config": SimulationConfig().model_dump(mode="json"),
            "presets": PRESETS,
            "distributions": {
                "failure": DEFAULT_FAILURE_DISTRIBUTION,
                "segment": DEFAULT_SEGMENT_DISTRIBUTION,
                "method": DEFAULT_METHOD_DISTRIBUTION,
                "abandonment_reason": DEFAULT_ABANDONMENT_REASONS,
            },
        }
    )


@router.post("/run")
async def run_simulation(config: SimulationConfig) -> dict:
    """Start a run (§35). Returns immediately; poll or stream for progress."""
    run_id = await simulation_service.start_run(config)
    return ok({"run_id": str(run_id), "status": str(SimulationStatus.RUNNING)})


@router.get("")
async def list_runs(
    limit: int = Query(20, ge=1, le=100), session: AsyncSession = Depends(db_session)
) -> dict:
    runs = (
        (
            await session.execute(
                select(SimulationRun).order_by(desc(SimulationRun.created_at)).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return ok([ser.simulation_run(r) for r in runs])


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(db_session)) -> dict:
    run = await _resolve(session, run_id)
    return ok(ser.simulation_run(run))


@router.get("/{run_id}/report")
async def run_report(run_id: uuid.UUID, session: AsyncSession = Depends(db_session)) -> dict:
    """The full simulation report (§56)."""
    run = await _resolve(session, run_id)

    baselines = {
        row.strategy: row.metrics
        for row in (
            await session.execute(
                select(BaselineResult).where(BaselineResult.simulation_run_id == run_id)
            )
        ).scalars()
    }
    totals = await metrics.revenue_totals(session, run_id=run_id)

    return ok(
        {
            "run": ser.simulation_run(run),
            "reproducibility": {
                "simulation_id": str(run.id),
                "run_ref": run.run_ref,
                "seed": run.seed,
                "config": run.config,
                "engine_version": run.engine_version,
                "data_version": run.data_version,
                "ai_mode": run.ai_mode,
                "ai_model": run.ai_model,
                "policy_snapshot": run.policy_snapshot,
                # A run is only bit-for-bit reproducible when no live model was involved.
                "deterministic": run.ai_mode == "heuristic",
            },
            "totals": ser.totals(totals),
            "scenarios": [
                ser.scenario_row(r)
                for r in await metrics.scenario_breakdown(session, run_id=run_id)
            ],
            "funnel": await metrics.pipeline_funnel(session, run_id=run_id),
            "ai_performance": await metrics.ai_performance(session, run_id=run_id),
            "baselines": [ser.baseline_row(m) for m in baselines.values()],
            "uplift": uplift(baselines),
        }
    )


@router.get("/{run_id}/stream")
async def stream_progress(run_id: uuid.UUID) -> EventSourceResponse:
    """Server-sent progress for the control centre (§35)."""

    async def publish():
        terminal = {str(SimulationStatus.COMPLETED), str(SimulationStatus.FAILED)}
        last_payload: str | None = None
        for _ in range(3600):  # hard ceiling so a stuck run cannot stream forever
            async with SessionLocal() as session:
                run = (
                    await session.execute(
                        select(SimulationRun).where(SimulationRun.id == run_id)
                    )
                ).scalar_one_or_none()
            if run is None:
                yield {"event": "error", "data": json.dumps({"error": "run not found"})}
                return

            payload = json.dumps(
                {
                    "run_id": str(run_id),
                    "status": str(run.status),
                    "progress": run.progress,
                    "error": run.error,
                }
            )
            if payload != last_payload:
                yield {"event": "progress", "data": payload}
                last_payload = payload

            if str(run.status) in terminal:
                yield {"event": "complete", "data": payload}
                return
            await asyncio.sleep(0.4)

    return EventSourceResponse(publish())


async def _resolve(session: AsyncSession, run_id: uuid.UUID) -> SimulationRun:
    run = (
        await session.execute(select(SimulationRun).where(SimulationRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"Simulation run {run_id} not found")
    return run
