"""§46 — webhook signature verification."""

import hashlib
import hmac

from app.integrations.razorpay.signature import (
    compute_signature,
    require_valid_signature,
    verify_webhook_signature,
)

SECRET = "whsec_test_recoverai"
BODY = b'{"event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_1"}}}}'


def test_valid_signature_is_accepted():
    sig = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(BODY, sig, secret=SECRET) is True


def test_tampered_body_is_rejected():
    sig = compute_signature(BODY, SECRET)
    tampered = BODY.replace(b"pay_1", b"pay_2")
    assert verify_webhook_signature(tampered, sig, secret=SECRET) is False


def test_wrong_secret_is_rejected():
    sig = compute_signature(BODY, "whsec_attacker")
    assert verify_webhook_signature(BODY, sig, secret=SECRET) is False


def test_missing_signature_is_rejected():
    assert verify_webhook_signature(BODY, None, secret=SECRET) is False
    assert verify_webhook_signature(BODY, "", secret=SECRET) is False


def test_unconfigured_secret_rejects_everything():
    """Absent a secret, no signature can be trusted — fail closed."""
    assert verify_webhook_signature(BODY, compute_signature(BODY, SECRET), secret=None) is False


def test_require_raises_on_invalid():
    import pytest

    from app.core.errors import WebhookSignatureError

    with pytest.raises(WebhookSignatureError):
        require_valid_signature(BODY, "deadbeef", secret=SECRET)


def test_signature_is_over_raw_bytes_not_reserialized_json():
    """Re-serializing the parsed body changes whitespace and breaks verification."""
    import json

    spaced = b'{"event": "payment.failed"}'
    compact = json.dumps(json.loads(spaced), separators=(",", ":")).encode()
    assert spaced != compact
    sig = compute_signature(spaced, SECRET)
    assert verify_webhook_signature(spaced, sig, secret=SECRET) is True
    assert verify_webhook_signature(compact, sig, secret=SECRET) is False
