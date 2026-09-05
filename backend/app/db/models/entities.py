"""Merchant-side entities: customers, orders, payments, checkouts, subscriptions.

These are the things that *generate* revenue at risk. They are populated identically
by the simulator and by Razorpay ingestion — `source` is the only difference.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk
from app.db.types import JSONType, UUIDType
from app.domain.enums import (
    CheckoutStatus,
    CustomerSegment,
    OrderStatus,
    PaymentStatus,
    Source,
    SubscriptionStatus,
)


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), default=Source.SIMULATOR, nullable=False)
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    name: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))

    segment: Mapped[str] = mapped_column(String(32), default=CustomerSegment.REGULAR, index=True)
    account_age_days: Mapped[int] = mapped_column(Integer, default=0)

    previous_transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    previous_success_count: Mapped[int] = mapped_column(Integer, default=0)
    previous_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    historical_success_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0)

    average_order_value_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    lifetime_value_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    preferred_payment_method: Mapped[str | None] = mapped_column(String(32))

    previous_checkout_count: Mapped[int] = mapped_column(Integer, default=0)
    previous_checkout_conversions: Mapped[int] = mapped_column(Integer, default=0)
    previous_checkout_conversion_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0)

    previous_subscription_count: Mapped[int] = mapped_column(Integer, default=0)
    previous_subscription_failures: Mapped[int] = mapped_column(Integer, default=0)
    previous_recoveries: Mapped[int] = mapped_column(Integer, default=0)

    #: Customer has asked not to be contacted. Checked before every outbound message;
    #: an opt-out is honoured even when the recovery action would otherwise message them.
    # server_default so the column can be added to a populated table without a rewrite.
    messaging_opt_out: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict)

    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = uuid_pk()
    order_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.CREATED, index=True)
    source: Mapped[str] = mapped_column(String(32), default=Source.SIMULATOR, index=True)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    order_metadata: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="orders")


class Payment(Base, TimestampMixin):
    """A payment is a lifecycle, not a verdict. FAILED is a state, not death."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = uuid_pk()
    payment_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    method: Mapped[str | None] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default=PaymentStatus.CREATED, index=True)

    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_category: Mapped[str | None] = mapped_column(String(48), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)

    source: Mapped[str] = mapped_column(String(32), default=Source.SIMULATOR, index=True)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)

    # --- recovery attribution (§40) ---
    is_recovery_payment: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    recovers_opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        # use_alter: breaks the payments <-> recovery_opportunities cycle at DDL time.
        ForeignKey("recovery_opportunities.id", ondelete="SET NULL", use_alter=True),
        index=True,
    )

    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    payment_metadata: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)

    __table_args__ = (Index("ix_payments_status_category", "status", "failure_category"),)


class PaymentEvent(Base, TimestampMixin):
    """Append-only lifecycle log for a payment."""

    __tablename__ = "payment_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("payments.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), default=Source.SIMULATOR)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict] = mapped_column(JSONType, default=dict)


class CheckoutSession(Base, TimestampMixin):
    __tablename__ = "checkout_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    checkout_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("orders.id", ondelete="SET NULL")
    )
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )

    cart_value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_count: Mapped[int] = mapped_column(Integer, default=1)
    products: Mapped[list] = mapped_column(JSONType, default=list)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(32), default=CheckoutStatus.STARTED, index=True)
    abandonment_reason: Mapped[str | None] = mapped_column(String(64))
    payment_method_intended: Mapped[str | None] = mapped_column(String(32))

    completed_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("payments.id", ondelete="SET NULL")
    )

    source: Mapped[str] = mapped_column(String(32), default=Source.SIMULATOR, index=True)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    subscription_ref: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )

    plan_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_name: Mapped[str] = mapped_column(String(128), nullable=False)
    billing_cycle: Mapped[str] = mapped_column(String(32), default="MONTHLY")
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_renewal_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default=SubscriptionStatus.ACTIVE, index=True)

    renewal_count: Mapped[int] = mapped_column(Integer, default=0)
    previous_successful_renewals: Mapped[int] = mapped_column(Integer, default=0)
    previous_failed_renewals: Mapped[int] = mapped_column(Integer, default=0)
    payment_method: Mapped[str | None] = mapped_column(String(32))

    source: Mapped[str] = mapped_column(String(32), default=Source.SIMULATOR, index=True)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)


class SubscriptionEvent(Base, TimestampMixin):
    """A renewal attempt or subscription lifecycle event."""

    __tablename__ = "subscription_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    renewal_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )

    cycle_number: Mapped[int] = mapped_column(Integer, default=1)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_category: Mapped[str | None] = mapped_column(String(48), index=True)

    # --- recovery attribution (§41) ---
    is_recovery_renewal: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    recovers_opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        # use_alter: breaks the payments <-> recovery_opportunities cycle at DDL time.
        ForeignKey("recovery_opportunities.id", ondelete="SET NULL", use_alter=True),
        index=True,
    )

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict] = mapped_column(JSONType, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
