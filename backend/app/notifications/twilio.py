"""Twilio channel — real WhatsApp and SMS delivery.

Uses Twilio's REST API directly over httpx rather than pulling in the SDK: one endpoint,
form-encoded, and it keeps the async story consistent with the rest of the codebase.

Credentials come from the environment and are never logged or returned to the frontend.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.enums import MessageChannel, MessageStatus
from app.notifications.base import (
    DeliveryResult,
    NotificationChannel,
    OutboundMessage,
    mask_recipient,
)

log = get_logger("recoverai.notifications.twilio")

API_ROOT = "https://api.twilio.com/2010-04-01"

#: Twilio statuses that mean "handed off successfully".
_ACCEPTED = {"queued", "accepted", "sending", "sent", "delivered"}

#: Twilio failures that are really *setup* problems, not code problems. Surfacing the
#: raw gateway string here sends people hunting through docs; these say what to do.
_GUIDANCE: tuple[tuple[str, str], ...] = (
    (
        "contentsid required",
        "WhatsApp only allows freeform text inside a 24-hour session, which opens when "
        "the customer messages you first. From the recipient's phone, WhatsApp "
        "'join <your-sandbox-keyword>' to the sandbox number, then retry.",
    ),
    (
        "not currently opted in",
        "The recipient has not joined the WhatsApp sandbox. From that phone, WhatsApp "
        "'join <your-sandbox-keyword>' to the sandbox number.",
    ),
    (
        "unverified",
        "Trial accounts can only message verified numbers. Verify the recipient in the "
        "Twilio console, or upgrade the account.",
    ),
    (
        "is not a valid phone number",
        "The number must be in E.164 form, e.g. +918080377833.",
    ),
)


def _explain(twilio_error: str) -> str | None:
    lowered = twilio_error.lower()
    for needle, guidance in _GUIDANCE:
        if needle in lowered:
            return guidance
    return None


class TwilioChannel(NotificationChannel):
    name = "twilio"

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        if not settings.twilio_configured:
            raise RuntimeError("Twilio credentials are not configured")
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None

    def _sender_for(self, channel: MessageChannel) -> str | None:
        if channel is MessageChannel.WHATSAPP:
            return settings.twilio_whatsapp_from
        return settings.twilio_sms_from

    @staticmethod
    def _address(channel: MessageChannel, number: str) -> str:
        """WhatsApp addresses are prefixed; SMS numbers are bare E.164."""
        cleaned = number.replace("whatsapp:", "").strip()
        return f"whatsapp:{cleaned}" if channel is MessageChannel.WHATSAPP else cleaned

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        sender = self._sender_for(message.channel)
        if not sender:
            return DeliveryResult(
                status=MessageStatus.FAILED,
                provider="twilio",
                error=f"no Twilio sender configured for {message.channel}",
            )

        url = f"{API_ROOT}/Accounts/{settings.twilio_account_sid}/Messages.json"
        base = {
            "From": self._address(message.channel, sender),
            "To": self._address(message.channel, message.to),
        }
        payload = {**base, "Body": message.body}

        try:
            response = await self._client.post(
                url,
                data=payload,
                auth=settings.twilio_auth,
            )
        except Exception as exc:
            log.warning("notification.twilio_call_failed", error=type(exc).__name__)
            return DeliveryResult(
                status=MessageStatus.FAILED,
                provider="twilio",
                error=f"{type(exc).__name__}: {exc}",
            )

        if response.status_code >= 400:
            # Twilio returns a structured error body; surface its message, not the token.
            detail = ""
            try:
                detail = str(response.json().get("message", ""))[:300]
            except Exception:
                detail = response.text[:300]
            log.warning(
                "notification.twilio_rejected",
                status_code=response.status_code,
                channel=str(message.channel),
            )
            # WhatsApp refuses freeform business messages on sandbox/trial tiers. If an
            # approved template is configured, deliver that rather than nothing — and say
            # so, because the recipient will not see the text RecoverAI composed.
            if "contentsid required" in detail.lower() and settings.twilio_content_sid:
                return await self._send_template(url, base, message, freeform_error=detail)

            guidance = _explain(detail)
            return DeliveryResult(
                status=MessageStatus.FAILED,
                provider="twilio",
                error=f"twilio {response.status_code}: {detail}",
                details={"guidance": guidance} if guidance else {},
            )

        data = response.json()
        twilio_status = str(data.get("status", "")).lower()
        accepted = twilio_status in _ACCEPTED

        log.info(
            "notification.twilio_sent",
            channel=str(message.channel),
            to=mask_recipient(message.to),
            twilio_status=twilio_status,
        )
        return DeliveryResult(
            # "Sent" means Twilio accepted it, not that the customer read it — and
            # certainly not that they paid.
            status=MessageStatus.SENT if accepted else MessageStatus.FAILED,
            delivered_externally=accepted,
            provider="twilio",
            provider_message_id=data.get("sid"),
            error=None if accepted else f"twilio status {twilio_status}",
            details={"twilio_status": twilio_status},
        )

    async def _send_template(
        self, url: str, base: dict, message: OutboundMessage, *, freeform_error: str
    ) -> DeliveryResult:
        """Deliver the approved template when our own wording is not permitted."""
        payload = {**base, "ContentSid": settings.twilio_content_sid}
        try:
            response = await self._client.post(
                url,
                data=payload,
                auth=settings.twilio_auth,
            )
        except Exception as exc:
            return DeliveryResult(
                status=MessageStatus.FAILED, provider="twilio",
                error=f"template fallback failed: {type(exc).__name__}: {exc}",
            )

        if response.status_code >= 400:
            try:
                detail = str(response.json().get("message", ""))[:300]
            except Exception:
                detail = response.text[:300]
            return DeliveryResult(
                status=MessageStatus.FAILED, provider="twilio",
                error=f"template fallback rejected: {detail}",
            )

        data = response.json()
        log.info(
            "notification.twilio_template_sent",
            to=mask_recipient(message.to),
            content_sid=settings.twilio_content_sid,
        )
        return DeliveryResult(
            status=MessageStatus.SENT,
            delivered_externally=True,
            provider="twilio",
            provider_message_id=data.get("sid"),
            body_substituted=True,
            details={
                "delivered_body": data.get("body"),
                "content_sid": settings.twilio_content_sid,
                "guidance": (
                    "WhatsApp would not carry RecoverAI's own wording on this account "
                    "tier, so an approved template was delivered instead. The composed "
                    "message is shown above; the recipient saw the template text. "
                    "Authoring a matching template requires a paid Twilio account."
                ),
                "freeform_error": freeform_error,
            },
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
