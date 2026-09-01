"""Atomic counters backing the human-readable refs (OPP0001, RA0001, ...).

A ref must be unique and monotonic even when webhooks arrive concurrently, so it is
allocated by an atomic UPDATE ... RETURNING rather than by counting rows.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RefCounter(Base):
    __tablename__ = "ref_counters"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
