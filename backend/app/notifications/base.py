"""Messaging channel interface.

A channel delivers a composed message. It knows nothing about recovery, revenue or
outcomes — and critically, a successful *delivery* says nothing about whether the
customer paid. That distinction is the whole reason this layer is separate from the
executor: sending is an action, paying is an outcome.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.domain.enums import MessageChannel, MessageStatus


@dataclass(slots=True)
class OutboundMessage:
    channel: MessageChannel
    to: str
    body: str
    template: str
    action_url: str | None = None


@dataclass(slots=True)
class DeliveryResult:
    status: MessageStatus
    #: True only when the message genuinely left this machine toward a real handset.
    delivered_externally: bool = False
    provider: str = "simulated"
    provider_message_id: str | None = None
    error: str | None = None
    details: dict = field(default_factory=dict)


class NotificationChannel(ABC):
    name: str

    @abstractmethod
    async def send(self, message: OutboundMessage) -> DeliveryResult:
        """Deliver the message. Must not raise for ordinary failure."""

    async def aclose(self) -> None:  # pragma: no cover - most channels hold nothing
        return None


def mask_recipient(value: str | None) -> str | None:
    """+919812345678 -> +9198XXXXXX78. Enough to identify, not enough to leak."""
    if not value:
        return None
    cleaned = value.replace("whatsapp:", "").strip()
    if len(cleaned) <= 6:
        return "X" * len(cleaned)
    return f"{cleaned[:4]}{'X' * (len(cleaned) - 6)}{cleaned[-2:]}"
