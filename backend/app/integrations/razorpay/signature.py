"""Razorpay webhook signature verification (§46).

The HMAC must be computed over the **raw request body**, byte for byte. Re-serializing
the parsed JSON changes whitespace and key order and silently breaks verification, so
the caller passes bytes and this module never sees a dict.
"""

from __future__ import annotations

import hashlib
import hmac

from app.core.config import settings
from app.core.errors import WebhookSignatureError


def compute_signature(raw_body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    raw_body: bytes, provided_signature: str | None, *, secret: str | None = None
) -> bool:
    """Constant-time comparison of the expected and provided signatures."""
    webhook_secret = secret or settings.razorpay_webhook_secret
    if not webhook_secret or not provided_signature:
        return False
    expected = compute_signature(raw_body, webhook_secret)
    # compare_digest, never ==, so signature comparison is not timing-attackable.
    return hmac.compare_digest(expected, provided_signature)


def require_valid_signature(
    raw_body: bytes, provided_signature: str | None, *, secret: str | None = None
) -> None:
    if not verify_webhook_signature(raw_body, provided_signature, secret=secret):
        raise WebhookSignatureError(
            "Razorpay webhook signature verification failed",
            details={"has_signature": bool(provided_signature)},
        )
