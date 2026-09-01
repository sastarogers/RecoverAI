"""Razorpay payloads must become the same internal event the simulator produces."""

from app.domain.enums import EventType, FailureCategory, Source
from app.ingestion.normalizer_razorpay import normalize


def _payment_failed(**overrides) -> dict:
    entity = {
        "id": "pay_MnO123", "amount": 500000, "currency": "INR", "method": "card",
        "order_id": "order_ABC", "error_code": "BAD_REQUEST_ERROR",
        "error_reason": "card_expired", "created_at": 1767225600,
        "notes": {"recoverai_customer_ref": "C0001"},
    }
    entity.update(overrides)
    return {
        "event": "payment.failed",
        "created_at": 1767225600,
        "payload": {"payment": {"entity": entity}},
    }


def test_payment_failed_is_normalized():
    event = normalize(_payment_failed(), razorpay_event_id="evt_1")
    assert event.source is Source.RAZORPAY
    assert event.event_type is EventType.PAYMENT_FAILED
    assert event.amount_minor == 500000, "Razorpay amounts are already paise"
    assert event.customer_ref == "C0001"
    assert event.external_ids["razorpay_payment_id"] == "pay_MnO123"
    assert event.opens_opportunity is True


def test_gateway_error_reason_maps_to_a_category():
    event = normalize(_payment_failed(), razorpay_event_id="evt_1")
    assert event.failure_category is FailureCategory.EXPIRED_CARD


def test_unmapped_failure_falls_back_to_unknown_not_a_guess():
    payload = _payment_failed(error_code="SOMETHING_NEW", error_reason="never_seen")
    event = normalize(payload, razorpay_event_id="evt_2")
    assert event.failure_category is FailureCategory.UNKNOWN


def test_dedupe_key_is_anchored_on_the_razorpay_event_id():
    a = normalize(_payment_failed(), razorpay_event_id="evt_1")
    b = normalize(_payment_failed(), razorpay_event_id="evt_1")
    c = normalize(_payment_failed(), razorpay_event_id="evt_2")
    assert a.dedupe_key == b.dedupe_key
    assert a.dedupe_key != c.dedupe_key


def test_recovery_attribution_is_extracted_from_notes():
    """§40: attribution travels in `notes`, not inferred from amount and timing."""
    payload = {
        "event": "payment.captured",
        "created_at": 1767225600,
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_REC1", "amount": 700000, "currency": "INR", "method": "upi",
                    "notes": {
                        "recoverai_opportunity_ref": "OPP0001",
                        "recoverai_attempt_ref": "RA0001",
                        "recoverai_customer_ref": "C0002",
                    },
                }
            }
        },
    }
    event = normalize(payload, razorpay_event_id="evt_3")
    assert event.event_type is EventType.PAYMENT_CAPTURED
    assert event.attribution["recoverai_opportunity_ref"] == "OPP0001"
    assert event.attribution["recoverai_attempt_ref"] == "RA0001"


def test_irrelevant_events_are_ignored():
    assert normalize({"event": "refund.created", "payload": {}}, razorpay_event_id="e") is None


def test_missing_event_field_is_rejected():
    import pytest

    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        normalize({"payload": {}}, razorpay_event_id="e")
