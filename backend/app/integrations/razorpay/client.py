"""Razorpay Test Mode client.

Test Mode only. Credentials come from the environment and are never returned to the
frontend — `status()` reports booleans and a masked key id.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.errors import IntegrationError
from app.core.logging import get_logger

log = get_logger("recoverai.razorpay")


@dataclass(slots=True)
class RazorpayStatus:
    configured: bool
    webhook_configured: bool
    enabled: bool
    key_id_masked: str | None
    reachable: bool | None = None
    error: str | None = None


def mask_key(key_id: str | None) -> str | None:
    if not key_id:
        return None
    return f"{key_id[:8]}…{key_id[-4:]}" if len(key_id) > 12 else "…"


class RazorpayClient:
    """Thin async wrapper over the (synchronous) Razorpay SDK."""

    def __init__(self) -> None:
        if not settings.razorpay_configured:
            raise IntegrationError("Razorpay credentials are not configured")
        import razorpay

        self._client = razorpay.Client(
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
        )
        self._client.set_app_details({"title": "RecoverAI", "version": "0.1.0"})

    async def _call(self, fn, *args, **kwargs) -> Any:
        """Run a blocking SDK call without stalling the event loop."""
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as exc:
            log.warning("razorpay.call_failed", error=type(exc).__name__)
            raise IntegrationError(f"Razorpay call failed: {type(exc).__name__}") from exc

    async def create_order(
        self, *, amount_minor: int, currency: str = "INR", notes: dict | None = None
    ) -> dict:
        """Create a Test Mode order. `amount` is already in paise."""
        return await self._call(
            self._client.order.create,
            {
                "amount": int(amount_minor),
                "currency": currency,
                "payment_capture": 1,
                "notes": notes or {},
            },
        )

    async def create_payment_link(
        self,
        *,
        amount_minor: int,
        description: str,
        customer: dict | None = None,
        notes: dict | None = None,
        currency: str = "INR",
    ) -> dict:
        """Create a Test Mode payment link carrying RecoverAI attribution in `notes`."""
        payload: dict[str, Any] = {
            "amount": int(amount_minor),
            "currency": currency,
            "description": description[:255],
            "notes": notes or {},
            "reminder_enable": False,
        }
        if customer:
            payload["customer"] = customer
            payload["notify"] = {"sms": False, "email": False}
        return await self._call(self._client.payment_link.create, payload)

    async def fetch_payment(self, payment_id: str) -> dict:
        return await self._call(self._client.payment.fetch, payment_id)

    async def fetch_order(self, order_id: str) -> dict:
        return await self._call(self._client.order.fetch, order_id)

    async def ping(self) -> bool:
        """Cheap reachability check against Test Mode."""
        try:
            await self._call(self._client.order.all, {"count": 1})
            return True
        except IntegrationError:
            return False


async def status() -> RazorpayStatus:
    base = RazorpayStatus(
        configured=settings.razorpay_configured,
        webhook_configured=settings.razorpay_webhook_configured,
        enabled=settings.razorpay_enabled,
        key_id_masked=mask_key(settings.razorpay_key_id),
    )
    if not base.configured:
        return base
    try:
        base.reachable = await RazorpayClient().ping()
    except IntegrationError as exc:
        base.reachable = False
        base.error = exc.message
    return base
