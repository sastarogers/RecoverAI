"""Background execution of simulation runs.

A run owns its own session and transaction so the HTTP request can return immediately
with a run id while the work continues. Progress is written to the run row, which is
what the SSE endpoint streams — no in-memory queue to lose on restart.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.ids import utcnow
from app.core.logging import get_logger
from app.db.models import SimulationRun
from app.db.session import session_scope
from app.domain.enums import SimulationStatus
from app.simulation.config import SimulationConfig
from app.simulation.runner import create_run, execute_run

log = get_logger("recoverai.simulation.service")

#: Tasks are kept referenced so the event loop cannot garbage-collect a running run.
_RUNNING: dict[uuid.UUID, asyncio.Task] = {}


async def start_run(config: SimulationConfig) -> uuid.UUID:
    """Create the run row, then execute it in the background."""
    async with session_scope() as session:
        run = await create_run(session, config)
        run_id = run.id

    task = asyncio.create_task(_execute(run_id, config))
    _RUNNING[run_id] = task
    task.add_done_callback(lambda _t: _RUNNING.pop(run_id, None))
    return run_id


async def _execute(run_id: uuid.UUID, config: SimulationConfig) -> None:
    try:
        async with session_scope() as session:
            run = (
                await session.execute(select(SimulationRun).where(SimulationRun.id == run_id))
            ).scalar_one()
            await execute_run(session, run, config)
    except Exception as exc:  # a failed run must be recorded, not silently lost
        log.error("simulation.failed", run_id=str(run_id), error=str(exc))
        try:
            async with session_scope() as session:
                run = (
                    await session.execute(
                        select(SimulationRun).where(SimulationRun.id == run_id)
                    )
                ).scalar_one()
                run.status = SimulationStatus.FAILED
                run.error = f"{type(exc).__name__}: {exc}"
                run.completed_at = utcnow()
                run.progress = {
                    **(run.progress or {}),
                    "stage": "Failed",
                    "message": str(exc),
                }
        except Exception:  # pragma: no cover - the database itself is unavailable
            log.error("simulation.failure_not_recorded", run_id=str(run_id))


def is_running(run_id: uuid.UUID) -> bool:
    return run_id in _RUNNING


async def run_synchronously(config: SimulationConfig) -> uuid.UUID:
    """Used by demo endpoints and tests, where the caller wants the finished result."""
    async with session_scope() as session:
        run = await create_run(session, config)
        await execute_run(session, run, config)
        return run.id
