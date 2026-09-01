"""RULE 6 — Razorpay events are idempotent, in the strongest sense that matters:
a redelivered success webhook must not recover the same money twice.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.core.ids import utcnow
from app.db.models import (
    RecoveryAttempt,
    RecoveryLedger,
    RecoveryOpportunity,
    WebhookEvent,
)
from app.domain.enums import (
    ExecutionStatus,
    ExecutorKind,
    OpportunityStatus,
    RecoveryAction,
    WebhookStatus,
)
from app.domain.state_machine import transition
from app.integrations.razorpay.webhooks import record_and_process
from tests.factories import make_customer, make_opportunity


def _failed_payload(payment_id="pay_FAIL1", customer_ref="C0001") -> dict:
    return {
        "event": "payment.failed",
        "created_at": 1767225600,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id, "amount": 500000, "currency": "INR", "method": "upi",
                    "error_code": "BANK_TIMEOUT", "error_reason": "issuer_down",
                    "notes": {"recoverai_customer_ref": customer_ref},
                }
            }
        },
    }


def _captured_payload(opportunity_ref, attempt_ref, payment_id="pay_REC1", amount=500000) -> dict:
    return {
        "event": "payment.captured",
        "created_at": 1767225601,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id, "amount": amount, "currency": "INR", "method": "upi",
                    "notes": {
                        "recoverai_opportunity_ref": opportunity_ref,
                        "recoverai_attempt_ref": attempt_ref,
                    },
                }
            }
        },
    }


async def _ledger_total(session) -> int:
    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(RecoveryLedger.recovered_amount_minor), 0))
            )
        ).scalar_one()
    )


async def _prepare_attempt(session, amount=500_000):
    """An opportunity awaiting confirmation from the gateway."""
    cust = make_customer()
    session.add(cust)
    await session.flush()
    opp = make_opportunity(cust, amount_minor=amount)
    opp.customer_id = cust.id
    session.add(opp)
    await session.flush()

    for target in (
        OpportunityStatus.ANALYZING,
        OpportunityStatus.RECOMMENDED,
        OpportunityStatus.APPROVED,
        OpportunityStatus.EXECUTING,
    ):
        transition(opp, target)

    attempt = RecoveryAttempt(
        attempt_ref="RA0001", opportunity_id=opp.id, attempt_number=1,
        action=RecoveryAction.PAYMENT_LINK, executor=ExecutorKind.RAZORPAY,
        execution_status=ExecutionStatus.PENDING, executed_at=utcnow(),
    )
    session.add(attempt)
    opp.attempt_count = 1
    await session.flush()
    return opp, attempt


async def test_duplicate_failure_webhook_creates_one_opportunity(session):
    payload = _failed_payload()
    first = await record_and_process(
        session, payload=payload, raw_event_id="evt_dup", signature_valid=True
    )
    second = await record_and_process(
        session, payload=payload, raw_event_id="evt_dup", signature_valid=True
    )

    assert first.status is WebhookStatus.PROCESSED
    assert second.status is WebhookStatus.DUPLICATE
    count = (
        await session.execute(select(func.count(RecoveryOpportunity.id)))
    ).scalar_one()
    assert count == 1


async def test_replayed_success_webhook_settles_once(session):
    """The headline guarantee: same event twice, money counted once."""
    opp, attempt = await _prepare_attempt(session, amount=500_000)
    payload = _captured_payload(opp.opportunity_ref, attempt.attempt_ref)

    first = await record_and_process(
        session, payload=payload, raw_event_id="evt_cap_1", signature_valid=True
    )
    second = await record_and_process(
        session, payload=payload, raw_event_id="evt_cap_1", signature_valid=True
    )

    assert first.settled_amount_minor == 500_000
    assert second.status is WebhookStatus.DUPLICATE
    assert await _ledger_total(session) == 500_000
    assert opp.status == OpportunityStatus.RECOVERED


async def test_distinct_event_ids_for_the_same_recovery_still_settle_once(session):
    """Even if Razorpay sends a *different* event id for the same recovery, the ledger
    holds one entry per opportunity."""
    opp, attempt = await _prepare_attempt(session, amount=500_000)

    await record_and_process(
        session,
        payload=_captured_payload(opp.opportunity_ref, attempt.attempt_ref, "pay_A"),
        raw_event_id="evt_A",
        signature_valid=True,
    )
    await record_and_process(
        session,
        payload=_captured_payload(opp.opportunity_ref, attempt.attempt_ref, "pay_B"),
        raw_event_id="evt_B",
        signature_valid=True,
    )

    assert await _ledger_total(session) == 500_000
    entries = (await session.execute(select(RecoveryLedger))).scalars().all()
    assert len(entries) == 1


async def test_invalid_signature_never_reaches_the_pipeline(session):
    result = await record_and_process(
        session, payload=_failed_payload(), raw_event_id="evt_bad", signature_valid=False
    )

    assert result.status is WebhookStatus.INVALID
    count = (await session.execute(select(func.count(RecoveryOpportunity.id)))).scalar_one()
    assert count == 0, "an unverified event must not create an opportunity"

    record = (await session.execute(select(WebhookEvent))).scalar_one()
    assert record.signature_valid is False
    assert record.processing_status == WebhookStatus.INVALID


async def test_unattributed_payment_is_not_counted_as_recovery(session):
    """§40: an ordinary purchase is not recovered revenue just because it succeeded."""
    opp, attempt = await _prepare_attempt(session)
    payload = {
        "event": "payment.captured",
        "created_at": 1767225601,
        "payload": {
            "payment": {
                "entity": {"id": "pay_UNRELATED", "amount": 500000, "currency": "INR",
                           "method": "upi", "notes": {}}
            }
        },
    }

    result = await record_and_process(
        session, payload=payload, raw_event_id="evt_unrelated", signature_valid=True
    )

    assert result.settled_amount_minor == 0
    assert "not counted as recovery" in result.message
    assert await _ledger_total(session) == 0
    assert opp.status != OpportunityStatus.RECOVERED


async def test_settlement_is_capped_at_the_amount_at_risk(session):
    """A webhook claiming a larger amount cannot inflate recovered revenue."""
    opp, attempt = await _prepare_attempt(session, amount=500_000)
    payload = _captured_payload(
        opp.opportunity_ref, attempt.attempt_ref, "pay_BIG", amount=9_999_999
    )

    await record_and_process(
        session, payload=payload, raw_event_id="evt_big", signature_valid=True
    )

    assert await _ledger_total(session) == 500_000


async def test_webhook_events_are_recorded_for_audit(session):
    await record_and_process(
        session, payload=_failed_payload(), raw_event_id="evt_audit", signature_valid=True
    )
    record = (await session.execute(select(WebhookEvent))).scalar_one()
    assert record.provider == "razorpay"
    assert record.event_id == "evt_audit"
    assert record.event_type == "payment.failed"
    assert record.processed_at is not None
