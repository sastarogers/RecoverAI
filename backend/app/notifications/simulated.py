"""Simulated channel — composes and records, delivers nothing.

The default. It keeps the whole feature demonstrable with no Twilio account, and it is
the *only* channel a simulation run is allowed to use: a 1,000-customer simulation that
could reach the real network would send a thousand real messages.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.enums import MessageStatus
from app.notifications.base import (
    DeliveryResult,
    NotificationChannel,
    OutboundMessage,
    mask_recipient,
)

log = get_logger("recoverai.notifications.simulated")


class SimulatedChannel(NotificationChannel):
    name = "simulated"

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        log.info(
            "notification.simulated",
            channel=str(message.channel),
            to=mask_recipient(message.to),
            template=message.template,
        )
        return DeliveryResult(
            status=MessageStatus.SIMULATED,
            delivered_externally=False,
            provider="simulated",
            details={"note": "composed and recorded; not delivered to a real handset"},
        )
