"""The recovery pipeline tables: opportunity, AI decision, policy decision,
attempt, outcome, and the ledger that holds the actual money.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk
from app.db.types import JSONType, UUIDType
from app.domain.enums import (
    ExecutionStatus,
    LedgerEntryType,
    OpportunityStatus,
    Source,
)

#: Predicate for the partial unique index that enforces one settlement per opportunity.
_RECOVERED_ONLY = text("entry_type = 'RECOVERED'")


class RecoveryOpportunity(Base, TimestampMixin):
    """The normalized unit of revenue at risk — the spine of the platform.

    Whatever the source and whatever the scenario, everything downstream operates on
    this row. `recovered_amount_minor` is written *only* by ledger settlement.
    """

    __tablename__ = "recovery_opportunities"

    id: Mapped[uuid.UUID] = uuid_pk()
    opportunity_ref: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    scenario: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), default=Source.SIMULATOR, index=True)
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )

    # Typed links — exactly one branch is populated per scenario.
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("payments.id", ondelete="CASCADE"), index=True
    )
    checkout_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("checkout_sessions.id", ondelete="CASCADE"), index=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    subscription_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("subscription_events.id", ondelete="CASCADE"), index=True
    )

    amount_at_risk_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    failure_category: Mapped[str | None] = mapped_column(String(48), index=True)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    reason_code: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(
        String(32), default=OpportunityStatus.DETECTED, nullable=False, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notification_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- money: written only by app.ledger.settlement ---
    recovered_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Subscriptions only. Reported separately; never added to recovered revenue (RULE 10).
    projected_retention_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    #: Exact observable context handed to the AI, for the audit trail.
    context_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)

    #: Idempotency: a duplicate webhook cannot create a second opportunity (RULE 6).
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    #: Run-independent identity of a *generated* opportunity, e.g. "payment:37".
    #: Display refs (OPP0001) are allocated globally and therefore shift between runs, so
    #: seeding randomness on them would make the same seed produce different outcomes in
    #: a used database. Hidden truth and outcome draws key on this instead.
    simulation_key: Mapped[str | None] = mapped_column(String(64), index=True)

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ai_decisions: Mapped[list[AIDecision]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", order_by="AIDecision.created_at"
    )
    attempts: Mapped[list[RecoveryAttempt]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="RecoveryAttempt.attempt_number",
    )

    __table_args__ = (
        CheckConstraint(
            "recovered_amount_minor >= 0 AND recovered_amount_minor <= amount_at_risk_minor",
            name="recovered_within_risk",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        Index("ix_opportunities_scenario_status", "scenario", "status"),
        Index("ix_opportunities_run_scenario", "simulation_run_id", "scenario"),
    )

    @property
    def is_recovered(self) -> bool:
        return self.status == OpportunityStatus.RECOVERED


class AIDecision(Base, TimestampMixin):
    """A validated recommendation. Never authoritative about money."""

    __tablename__ = "ai_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), index=True
    )
    #: Nth decision made for this opportunity. A blocked recommendation still consumed
    #: a decision even though no attempt was executed, so this is not the attempt number.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The attempt this recommendation was aimed at (attempts only advance on execution).
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    recovery_probability: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)

    decision_source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    context_signature: Mapped[str | None] = mapped_column(String(64), index=True)

    raw_response: Mapped[dict] = mapped_column(JSONType, default=dict)
    validation_errors: Mapped[list | None] = mapped_column(JSONType)

    opportunity: Mapped[RecoveryOpportunity] = relationship(back_populates="ai_decisions")

    __table_args__ = (
        UniqueConstraint("opportunity_id", "sequence", name="uq_ai_decision_sequence"),
        CheckConstraint(
            "recovery_probability >= 0 AND recovery_probability <= 1", name="probability_range"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )


class PolicyDecision(Base, TimestampMixin):
    """The deterministic gate between AI and execution. AI cannot bypass it (RULE 4)."""

    __tablename__ = "policy_decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), index=True
    )
    ai_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("ai_decisions.id", ondelete="CASCADE"), index=True
    )

    verdict: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    requested_action: Mapped[str] = mapped_column(String(48), nullable=False)
    effective_action: Mapped[str | None] = mapped_column(String(48))
    blocked_by_rule: Mapped[str | None] = mapped_column(String(48), index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: Full trace: every rule id with pass/fail and its message.
    rules_evaluated: Mapped[list] = mapped_column(JSONType, default=list)

    __table_args__ = (
        UniqueConstraint("opportunity_id", "ai_decision_id", name="uq_policy_per_ai_decision"),
    )


class RecoveryAttempt(Base, TimestampMixin):
    __tablename__ = "recovery_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    attempt_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    ai_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("ai_decisions.id", ondelete="SET NULL")
    )
    policy_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("policy_decisions.id", ondelete="SET NULL")
    )

    executor: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_status: Mapped[str] = mapped_column(
        String(32), default=ExecutionStatus.PENDING, nullable=False, index=True
    )
    #: e.g. a Razorpay payment-link / order id created for this attempt.
    external_ref: Mapped[str | None] = mapped_column(String(128), index=True)
    #: Round-trip token embedded in Razorpay `notes` so the settling payment is attributable.
    attribution_token: Mapped[str | None] = mapped_column(String(128), index=True)

    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cost_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)

    opportunity: Mapped[RecoveryOpportunity] = relationship(back_populates="attempts")
    outcome: Mapped[RecoveryOutcome | None] = relationship(
        back_populates="attempt", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("opportunity_id", "attempt_number", name="uq_attempt_number"),
    )


class RecoveryOutcome(Base, TimestampMixin):
    """The observed result of an attempt. One per attempt, enforced by UNIQUE."""

    __tablename__ = "recovery_outcomes"

    id: Mapped[uuid.UUID] = uuid_pk()
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("recovery_attempts.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), index=True
    )

    outcome: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    realized_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(48), nullable=False)
    #: Payment ref / renewal ref / Razorpay payment id that proves the outcome.
    evidence_ref: Mapped[str | None] = mapped_column(String(128), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict] = mapped_column(JSONType, default=dict)

    attempt: Mapped[RecoveryAttempt] = relationship(back_populates="outcome")

    __table_args__ = (
        CheckConstraint("realized_amount_minor >= 0", name="realized_non_negative"),
    )


class RecoveryLedger(Base, TimestampMixin):
    """Actual recovered revenue. The only table the dashboard trusts for money.

    Three independent guards make double-counting impossible (RULE 3):
      1. `settlement_key` UNIQUE — replaying the same outcome is a no-op.
      2. partial UNIQUE index on `opportunity_id WHERE entry_type='RECOVERED'`.
      3. CHECK that a recovery never exceeds the amount originally at risk.
    """

    __tablename__ = "recovery_ledger"

    id: Mapped[uuid.UUID] = uuid_pk()
    entry_type: Mapped[str] = mapped_column(
        String(32), default=LedgerEntryType.RECOVERED, nullable=False
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("recovery_opportunities.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("recovery_outcomes.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("recovery_attempts.id", ondelete="CASCADE"), nullable=False
    )
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )

    scenario: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    original_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recovered_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str | None] = mapped_column(String(48))
    attempt_number: Mapped[int | None] = mapped_column(Integer)

    settlement_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint(
            "recovered_amount_minor > 0 AND recovered_amount_minor <= original_amount_minor",
            name="recovered_within_original",
        ),
        # The hard guarantee: at most one RECOVERED entry per opportunity, forever.
        Index(
            "uq_ledger_one_recovery_per_opportunity",
            "opportunity_id",
            unique=True,
            postgresql_where=_RECOVERED_ONLY,
            sqlite_where=_RECOVERED_ONLY,
        ),
    )

