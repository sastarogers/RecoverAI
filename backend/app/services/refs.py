"""Allocation of human-readable reference ids."""

from __future__ import annotations

from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import ref as format_ref
from app.db.models import RefCounter


async def next_number(session: AsyncSession, name: str, *, count: int = 1) -> int:
    """Reserve `count` consecutive numbers and return the first one.

    Atomic under concurrency: the UPDATE ... RETURNING is a single statement, so two
    webhooks racing for OPP refs can never receive the same number.
    """
    stmt = (
        update(RefCounter)
        .where(RefCounter.name == name)
        .values(value=RefCounter.value + count)
        .returning(RefCounter.value)
    )
    result = (await session.execute(stmt)).scalar_one_or_none()
    if result is None:
        # First use of this prefix. The INSERT races other writers, so it must run in
        # a SAVEPOINT: on Postgres a failed statement aborts the whole transaction, and
        # every later command — including the retry below — then fails with
        # InFailedSQLTransactionError. Catching IntegrityError alone is not enough.
        try:
            async with session.begin_nested():
                await session.execute(insert(RefCounter).values(name=name, value=count))
            result = count
        except IntegrityError:
            # Another writer created it first; the savepoint rolled back, so the
            # transaction is still usable and the update now finds the row.
            result = (await session.execute(stmt)).scalar_one()
    return int(result) - count + 1


async def next_ref(session: AsyncSession, prefix: str, *, width: int = 4) -> str:
    return format_ref(prefix, await next_number(session, prefix), width)


async def next_refs(session: AsyncSession, prefix: str, count: int, *, width: int = 4) -> list[str]:
    start = await next_number(session, prefix, count=count)
    return [format_ref(prefix, start + i, width) for i in range(count)]
