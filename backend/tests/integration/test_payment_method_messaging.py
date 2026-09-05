"""Expired / broken payment method → WhatsApp or SMS.

The feature is only useful where the customer is the only one who can fix the problem,
and it is only *safe* if a sent message never becomes revenue. Both are asserted here.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.core.ids import utcnow
from app.db.models import NotificationMessage, RecoveryAttempt, RecoveryLedger
from app.domain.enums import (
    ExecutionStatus,
    ExecutorKind,
    FailureCategory,
    MessageChannel,
    MessageStatus,
    OpportunityStatus,
    RecoveryAction,
    Scenario,
    Source,
)
from app.domain.state_machine import transition
from app.notifications.service import notify_payment_method_expired
from tests.factories import make_customer, make_opportunity


async def _setup(session, *, category=FailureCategory.EXPIRED_CARD, code="CARD_EXPIRED",
                 phone="+919812345678", opt_out=False, source=Source.SIMULATOR):
    cust = make_customer(phone=phone, messaging_opt_out=opt_out)
    session.add(cust)
    await session.flush()
    opp = make_opportunity(cust, amount_minor=99_900)
    opp.customer_id = cust.id
    opp.scenario = Scenario.FAILED_SUBSCRIPTION
    opp.failure_category = category
    opp.failure_code = code
    opp.source = source
    session.add(opp)
    await session.flush()

    for target in (
        OpportunityStatus.ANALYZING, OpportunityStatus.RECOMMENDED,
        OpportunityStatus.APPROVED, OpportunityStatus.EXECUTING,
    ):
        transition(opp, target)
    attempt = RecoveryAttempt(
        attempt_ref="RA0001", opportunity_id=opp.id, attempt_number=1,
        action=RecoveryAction.PAYMENT_UPDATE_REQUEST, executor=ExecutorKind.SIMULATOR,
        execution_status=ExecutionStatus.EXECUTED, executed_at=utcnow(),
    )
    session.add(attempt)
    await session.flush()
    return cust, opp, attempt


async def test_expired_card_produces_a_message(session):
    cust, opp, attempt = await _setup(session)

    result = await notify_payment_method_expired(
        session, opportunity=opp, customer=cust, attempt=attempt,
        action_url="https://rzp.io/i/demo", plan_name="Pro Monthly",
    )

    assert result.status is MessageStatus.SIMULATED
    message = (await session.execute(select(NotificationMessage))).scalar_one()
    assert message.channel in (MessageChannel.WHATSAPP, MessageChannel.SMS)
    assert "expired" in message.body.lower()
    assert "₹999" in message.body
    assert "Pro Monthly" in message.body
    assert message.action_url == "https://rzp.io/i/demo"
    # Number is stored masked, never in full.
    assert message.recipient_masked and "9812345678" not in message.recipient_masked


async def test_a_sent_message_is_not_recovered_revenue(session):
    """The whole point: messaging is an action, paying is an outcome."""
    cust, opp, attempt = await _setup(session)

    await notify_payment_method_expired(
        session, opportunity=opp, customer=cust, attempt=attempt, action_url="https://x"
    )

    total = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(RecoveryLedger.recovered_amount_minor), 0))
            )
        ).scalar_one()
    )
    assert total == 0
    assert opp.recovered_amount_minor == 0
    assert opp.status != OpportunityStatus.RECOVERED


async def test_retryable_failures_are_not_messaged(session):
    """A bank timeout is not the customer's problem to fix — don't spend a contact on it."""
    cust, opp, attempt = await _setup(
        session, category=FailureCategory.TEMPORARY, code="BANK_TIMEOUT"
    )

    result = await notify_payment_method_expired(
        session, opportunity=opp, customer=cust, attempt=attempt
    )

    assert result.sent is False
    assert result.status is MessageStatus.SKIPPED
    assert (await session.execute(select(func.count(NotificationMessage.id)))).scalar_one() == 0


async def test_opt_out_is_honoured(session):
    cust, opp, attempt = await _setup(session, opt_out=True)

    result = await notify_payment_method_expired(
        session, opportunity=opp, customer=cust, attempt=attempt
    )

    assert result.sent is False
    assert result.status is MessageStatus.SKIPPED
    message = (await session.execute(select(NotificationMessage))).scalar_one()
    # Recorded, not silently dropped — the decision is auditable.
    assert message.status == MessageStatus.SKIPPED
    assert "opted out" in (message.reason or "")
    assert message.body == ""


async def test_missing_phone_number_is_recorded_not_crashed(session):
    cust, opp, attempt = await _setup(session, phone=None)

    result = await notify_payment_method_expired(
        session, opportunity=opp, customer=cust, attempt=attempt
    )

    assert result.status is MessageStatus.SKIPPED
    assert "no contact number" in result.reason


async def test_simulated_run_never_reaches_the_real_network(session, monkeypatch):
    """A 1,000-customer simulation must not send 1,000 real WhatsApp messages."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "messaging_enabled", True, raising=False)
    monkeypatch.setattr(app_settings, "twilio_account_sid", "AC_fake", raising=False)
    monkeypatch.setattr(app_settings, "twilio_auth_token", "tok_fake", raising=False)
    monkeypatch.setattr(app_settings, "twilio_whatsapp_from", "whatsapp:+14155238886", raising=False)

    cust, opp, attempt = await _setup(session, source=Source.SIMULATOR)
    result = await notify_payment_method_expired(
        session, opportunity=opp, customer=cust, attempt=attempt
    )

    assert result.sent is False, "a simulated opportunity reached the live provider"
    message = (await session.execute(select(NotificationMessage))).scalar_one()
    assert message.provider == "simulated"
    assert message.delivered_externally is False


async def test_mandate_revoked_is_messaged_but_a_wrong_otp_is_not(session):
    """CUSTOMER_ACTION_REQUIRED splits: a dead mandate needs a new method, an OTP slip doesn't."""
    cust_a, opp_a, att_a = await _setup(
        session, category=FailureCategory.CUSTOMER_ACTION_REQUIRED, code="MANDATE_REVOKED"
    )
    assert (
        await notify_payment_method_expired(
            session, opportunity=opp_a, customer=cust_a, attempt=att_a
        )
    ).status is MessageStatus.SIMULATED

    cust_b = make_customer("C0002", phone="+919812345679")
    session.add(cust_b)
    await session.flush()
    opp_b = make_opportunity(cust_b, ref="OPP0002")
    opp_b.customer_id = cust_b.id
    opp_b.failure_category = FailureCategory.CUSTOMER_ACTION_REQUIRED
    opp_b.failure_code = "OTP_INCORRECT"
    opp_b.dedupe_key = "SIMULATOR:FAILED_PAYMENT:OPP0002"
    session.add(opp_b)
    await session.flush()

    assert (
        await notify_payment_method_expired(session, opportunity=opp_b, customer=cust_b)
    ).status is MessageStatus.SKIPPED
