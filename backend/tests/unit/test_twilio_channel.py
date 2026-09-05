"""Twilio adapter — request shape, channel addressing, and failure handling.

Exercised against a mock transport so the contract is pinned without an account and
without any risk of a real message going out during a test run.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.domain.enums import MessageChannel, MessageStatus
from app.notifications.base import OutboundMessage, mask_recipient
from app.notifications.twilio import TwilioChannel


@pytest.fixture
def twilio_env(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest123", raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", "secret-token", raising=False)
    monkeypatch.setattr(settings, "twilio_whatsapp_from", "whatsapp:+14155238886", raising=False)
    monkeypatch.setattr(settings, "twilio_sms_from", "+15005550006", raising=False)


def _channel(handler) -> TwilioChannel:
    return TwilioChannel(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_whatsapp_addresses_are_prefixed(twilio_env):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(201, json={"sid": "SM123", "status": "queued"})

    result = await _channel(handler).send(
        OutboundMessage(
            channel=MessageChannel.WHATSAPP, to="+919812345678",
            body="update your card", template="payment_method_update",
        )
    )

    assert seen["From"] == "whatsapp:+14155238886"
    assert seen["To"] == "whatsapp:+919812345678"
    assert seen["Body"] == "update your card"
    assert result.status is MessageStatus.SENT
    assert result.delivered_externally is True
    assert result.provider_message_id == "SM123"


async def test_sms_addresses_are_bare(twilio_env):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(201, json={"sid": "SM456", "status": "sent"})

    await _channel(handler).send(
        OutboundMessage(
            channel=MessageChannel.SMS, to="whatsapp:+919812345678",
            body="update your card", template="payment_method_update",
        )
    )

    assert seen["From"] == "+15005550006"
    assert seen["To"] == "+919812345678", "the whatsapp: prefix must be stripped for SMS"


async def test_twilio_rejection_is_reported_not_raised(twilio_env):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "The 'To' number is not a valid phone number"})

    result = await _channel(handler).send(
        OutboundMessage(
            channel=MessageChannel.SMS, to="nonsense", body="x", template="t"
        )
    )

    assert result.status is MessageStatus.FAILED
    assert result.delivered_externally is False
    assert "not a valid phone number" in (result.error or "")


async def test_network_failure_is_reported_not_raised(twilio_env):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    result = await _channel(handler).send(
        OutboundMessage(channel=MessageChannel.SMS, to="+919812345678", body="x", template="t")
    )

    assert result.status is MessageStatus.FAILED
    assert result.delivered_externally is False


async def test_credentials_never_appear_in_the_result(twilio_env):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "bad request"})

    result = await _channel(handler).send(
        OutboundMessage(channel=MessageChannel.SMS, to="+919812345678", body="x", template="t")
    )
    blob = f"{result.error} {result.details} {result.provider}"
    assert "secret-token" not in blob
    assert "ACtest123" not in blob


def test_construction_requires_credentials(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)
    with pytest.raises(RuntimeError):
        TwilioChannel()


@pytest.mark.parametrize(
    "raw,expected",
    [("+919812345678", "+919XXXXXXX78"), ("whatsapp:+919812345678", "+919XXXXXXX78"), (None, None)],
)
def test_masking(raw, expected):
    assert mask_recipient(raw) == expected
