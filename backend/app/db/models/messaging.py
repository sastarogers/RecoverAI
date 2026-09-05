"""Outbound customer messages.

Every message RecoverAI composes is recorded here whether or not it was delivered —
including the ones deliberately not sent. A message is a *recovery action*, never a
recovery: nothing in this table can make revenue recovered, and the ledger has no
foreign key to it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk
from app.db.types import JSONType, UUIDType
from app.domain.enums import MessageStatus


class NotificationMessage(Base, TimestampMixin):
    __tablename__ = "notification_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), index=True
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("recovery_attempts.id", ondelete="SET NULL"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )

    channel: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    template: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Stored masked (+9198XXXXXX56). The full number stays on the customer record.
    recipient_masked: Mapped[str | None] = mapped_column(String(48))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: The attributed payment link the message points at, if any.
    action_url: Mapped[str | None] = mapped_column(String(512))

    status: Mapped[str] = mapped_column(
        String(16), default=MessageStatus.QUEUED, nullable=False, index=True
    )
    #: True only when the message actually left the machine to a real handset.
    delivered_externally: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(128), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(String(128))

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)

    __table_args__ = (Index("ix_messages_opportunity_channel", "opportunity_id", "channel"),)
