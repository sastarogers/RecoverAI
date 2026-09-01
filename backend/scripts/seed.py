"""Seed a demo dataset.

Runs one full simulation through the real pipeline so the dashboard has something to
show on a fresh install. Safe to re-run: refs are globally unique, so a second seed adds
a second run rather than colliding with the first.

    python -m scripts.seed [--preset demo|competition|stress] [--seed N] [--reset]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.core.money import format_inr
from app.db.session import session_scope
from app.simulation.config import PRESETS, SimulationConfig
from app.simulation.runner import create_run, execute_run

#: Truncated in dependency-safe order by CASCADE.
RESET_TABLES = "customers, simulation_runs, ref_counters, webhook_events, audit_logs"


async def reset() -> None:
    async with session_scope() as session:
        await session.execute(text(f"TRUNCATE {RESET_TABLES} CASCADE"))
    print("• cleared existing data")


async def seed(preset: str, seed_value: int, ai_mode: str) -> int:
    config = SimulationConfig(seed=seed_value, ai_mode=ai_mode, **PRESETS[preset])

    print(f"• running '{preset}' preset (seed {seed_value}, engine {ai_mode})")
    async with session_scope() as session:
        run = await create_run(session, config)
        summary = await execute_run(session, run, config)

    c = summary.counters
    at_risk = c.get("revenue_at_risk_minor", 0)
    recovered = c.get("recovered_revenue_minor", 0)

    print(f"\n  run                {summary.run_ref}")
    print(f"  customers          {c.get('customers', 0):,}")
    print(f"  opportunities      {c.get('opportunities', 0):,}")
    print(f"  revenue at risk    {format_inr(at_risk)}")
    print(f"  recovered revenue  {format_inr(recovered)}")
    print(f"  recovery rate      {(recovered / at_risk if at_risk else 0):.1%}")
    print(f"  attempts           {c.get('recovery_attempts', 0):,}")
    print(f"  blocked by policy  {c.get('blocked_actions', 0):,}")
    if c.get("uplift_minor"):
        print(f"  uplift vs baseline {format_inr(c['uplift_minor'])}")
    print("\n  open http://localhost:3000")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed RecoverAI with a demo dataset")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="demo")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--ai-mode",
        choices=("heuristic", "auto", "llm"),
        default="heuristic",
        help="heuristic keeps the run fully reproducible",
    )
    parser.add_argument("--reset", action="store_true", help="clear existing data first")
    args = parser.parse_args()

    async def run() -> int:
        if args.reset:
            await reset()
        return await seed(args.preset, args.seed, args.ai_mode)

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
