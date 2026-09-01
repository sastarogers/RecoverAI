"""Simulation, hidden ground truth, baselines, webhooks and audit."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk
from app.db.types import JSONType, UUIDType
from app.domain.enums import SimulationStatus, WebhookStatus


class SimulationRun(Base, TimestampMixin):
    """Everything needed to reproduce an experiment (§57)."""

    __tablename__ = "simulation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)

    status: Mapped[str] = mapped_column(
        String(24), default=SimulationStatus.PENDING, nullable=False, index=True
    )
    #: {stage, stage_index, total_stages, percent, counters:{...}, message}
    progress: Mapped[dict] = mapped_column(JSONType, default=dict)

    ai_mode: Mapped[str] = mapped_column(String(24), default="auto")
    ai_model: Mapped[str | None] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(24), default="1.0.0")
    data_version: Mapped[str] = mapped_column(String(24), default="1.0.0")
    policy_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    results: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(128))


class SimulationGroundTruth(Base, TimestampMixin):
    """RESTRICTED — the simulator's hidden reality (§17/§18).

    Written at generation time, read *only* by the outcome engine after a decision has
    been made and persisted, plus by post-hoc analytics. The context builder has no
    import path to this module; `tests/unit/test_ground_truth_isolation.py` enforces it.
    """

    __tablename__ = "simulation_ground_truth"

    id: Mapped[uuid.UUID] = uuid_pk()
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("recovery_opportunities.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    #: {"DELAYED_RETRY": 0.82, "IMMEDIATE_RETRY": 0.45, ...}
    action_success_probs: Mapped[dict] = mapped_column(JSONType, nullable=False)
    #: Unobservable drivers behind those probabilities.
    latent_factors: Mapped[dict] = mapped_column(JSONType, default=dict)
    optimal_action: Mapped[str] = mapped_column(String(48), nullable=False)
    optimal_probability: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    #: Some opportunities are genuinely dead — which is what makes STOP a real answer.
    is_recoverable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BaselineResult(Base, TimestampMixin):
    """Counterfactual replay of an alternative strategy over the same ground truth."""

    __tablename__ = "baseline_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    simulation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    strategy: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)

    __table_args__ = (
        UniqueConstraint("simulation_run_id", "strategy", name="uq_baseline_per_run"),
    )


class WebhookEvent(Base, TimestampMixin):
    """Idempotent inbound webhook log (RULE 6).

    UNIQUE(provider, event_id) is the idempotency key: a replayed Razorpay delivery
    hits the constraint, is marked DUPLICATE, and never re-enters the pipeline.
    """

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(32), default="razorpay", nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(64), index=True)

    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)

    processing_status: Mapped[str] = mapped_column(
        String(24), default=WebhookStatus.RECEIVED, nullable=False, index=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("recovery_opportunities.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
        Index("ix_webhook_received", "received_at"),
    )


class AuditLog(Base, TimestampMixin):
    """Answers §59: what happened, when, why, who decided, what was executed."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
