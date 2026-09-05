"""Razorpay Test Mode executor.

The defining property of this executor is what it *cannot* do: it never reports a
recovery as successful. It creates a real Test Mode artifact the customer can actually
pay — carrying RecoverAI attribution in `notes` — and then returns `PENDING`. The only
thing that can turn that into recovered revenue is an inbound `payment.captured` /
`order.paid` webhook carrying the same attribution (§23, RULE 2).

That asymmetry is deliberate. A recovery executor that can declare its own success is
indistinguishable from one that fabricates revenue.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.core.money import to_major
from app.db.models import Customer, RecoveryAttempt, RecoveryOpportunity
from app.domain.enums import (
    ExecutorKind,
    FailureCategory,
    Outcome,
    RecoveryAction,
    Scenario,
    requires_payment_method_update,
)
from app.executor.base import ExecutionResult, RecoveryExecutor
from app.integrations.razorpay.client import RazorpayClient

log = get_logger("recoverai.executor.razorpay")

#: Customer-facing wording per action. The artifact is the same (a payment link); what
#: differs is how the ask is framed, which is what the action actually decides.
_DESCRIPTIONS: dict[RecoveryAction, str] = {
    RecoveryAction.IMMEDIATE_RETRY: "Complete your payment for order {ref}",
    RecoveryAction.DELAYED_RETRY: "Your payment did not go through — try again for {ref}",
    RecoveryAction.RETRY_SUBSCRIPTION: "Renew your subscription ({ref})",
    RecoveryAction.PAYMENT_LINK: "Secure payment link for {ref}",
    RecoveryAction.CHECKOUT_RESUME: "Resume your checkout ({ref})",
    RecoveryAction.REMINDER: "Reminder: your order {ref} is waiting",
    RecoveryAction.DISCOUNT_INCENTIVE: "A discount on your order {ref}",
    RecoveryAction.ALTERNATE_PAYMENT_METHOD: "Pay for {ref} with another method",
    RecoveryAction.PAYMENT_UPDATE_REQUEST: "Update your payment method for {ref}",
    RecoveryAction.CUSTOMER_NOTIFICATION: "Action needed on your payment for {ref}",
    RecoveryAction.GRACE_PERIOD: "Your subscription {ref} is in a grace period",
}

#: Actions that put a payable artifact in front of the customer. GRACE_PERIOD and STOP
#: deliberately create nothing.
_CREATES_PAYMENT_LINK: frozenset[RecoveryAction] = frozenset(
    a for a in RecoveryAction if a not in (RecoveryAction.STOP, RecoveryAction.GRACE_PERIOD)
)


class RazorpayExecutor(RecoveryExecutor):
    """Executes recovery actions against Razorpay Test Mode."""

    kind = ExecutorKind.RAZORPAY

    def __init__(self, session: AsyncSession, *, client: RazorpayClient | None = None) -> None:
        self.session = session
        self._client = client

    def _get_client(self) -> RazorpayClient:
        if self._client is None:
            self._client = RazorpayClient()
        return self._client

    async def execute(
        self,
        *,
        opportunity: RecoveryOpportunity,
        attempt: RecoveryAttempt,
        action: RecoveryAction,
    ) -> ExecutionResult:
        if not settings.razorpay_configured:
            return ExecutionResult(
                executed=False,
                outcome=Outcome.PENDING,
                error="Razorpay is not configured; no recovery artifact could be created",
                details={"action": str(action)},
            )

        if action not in _CREATES_PAYMENT_LINK:
            # A grace period is a real decision, but it creates nothing to pay. Whether
            # it works is still decided by a later renewal event, never by us.
            return ExecutionResult(
                executed=True,
                outcome=Outcome.PENDING,
                details={"action": str(action), "artifact": "none"},
            )

        # Attribution travels with the artifact. Without it, a successful payment cannot
        # be tied back to this opportunity and is treated as an ordinary purchase (§40).
        notes = {
            "recoverai_opportunity_ref": opportunity.opportunity_ref,
            "recoverai_attempt_ref": attempt.attempt_ref,
            "recoverai_scenario": str(opportunity.scenario),
            "recoverai_action": str(action),
        }
        customer = (
            await self.session.execute(
                select(Customer).where(Customer.id == opportunity.customer_id)
            )
        ).scalar_one_or_none()
        if customer is not None:
            notes["recoverai_customer_ref"] = customer.customer_ref

        template = _DESCRIPTIONS.get(action, "Complete your payment for {ref}")
        description = template.format(ref=opportunity.opportunity_ref)

        try:
            link = await self._get_client().create_payment_link(
                amount_minor=int(opportunity.amount_at_risk_minor),
                description=description,
                currency=opportunity.currency,
                notes=notes,
            )
        except IntegrationError as exc:
            log.warning(
                "razorpay.execution_failed",
                opportunity_ref=opportunity.opportunity_ref,
                action=str(action),
                error=exc.message,
            )
            return ExecutionResult(
                executed=False,
                outcome=Outcome.PENDING,
                error=exc.message,
                details={"action": str(action)},
            )

        log.info(
            "razorpay.recovery_artifact_created",
            opportunity_ref=opportunity.opportunity_ref,
            attempt_ref=attempt.attempt_ref,
            action=str(action),
            payment_link_id=link.get("id"),
        )

        # When the instrument itself is dead, a link alone is not enough — the customer
        # has to be told, on a channel they actually read. The message carries the same
        # attributed link, so paying it settles this opportunity and nothing else.
        messaging = await self._notify_if_payment_method_dead(
            opportunity=opportunity, attempt=attempt, action=action,
            action_url=link.get("short_url"), customer=customer,
        )

        return ExecutionResult(
            executed=True,
            # PENDING, always. Only an inbound webhook can make this a recovery.
            outcome=Outcome.PENDING,
            external_ref=link.get("id"),
            attribution_token=opportunity.opportunity_ref,
            details={
                "action": str(action),
                "artifact": "payment_link",
                "short_url": link.get("short_url"),
                "amount": to_major(int(opportunity.amount_at_risk_minor)),
                "scenario": str(Scenario(opportunity.scenario)),
                "awaiting": "payment.captured webhook carrying recoverai_opportunity_ref",
                **({"messaging": messaging} if messaging else {}),
            },
        )

    async def _notify_if_payment_method_dead(
        self,
        *,
        opportunity: RecoveryOpportunity,
        attempt: RecoveryAttempt,
        action: RecoveryAction,
        action_url: str | None,
        customer: Customer | None,
    ) -> dict | None:
        """Send the 'update your payment method' message, when that is the real problem.

        Returns a summary for the attempt record. Messaging failures never fail the
        attempt: the payment link exists either way, and a recovery must not hinge on
        an SMS gateway being up.
        """
        category = FailureCategory(opportunity.failure_category or FailureCategory.UNKNOWN)
        if not requires_payment_method_update(category, opportunity.failure_code):
            return None
        if action not in (
            RecoveryAction.PAYMENT_UPDATE_REQUEST,
            RecoveryAction.ALTERNATE_PAYMENT_METHOD,
            RecoveryAction.CUSTOMER_NOTIFICATION,
        ):
            return None
        if customer is None:
            return None

        from app.notifications.service import notify_payment_method_expired

        plan_name = None
        if opportunity.subscription_id:
            from app.db.models import Subscription

            subscription = (
                await self.session.execute(
                    select(Subscription).where(Subscription.id == opportunity.subscription_id)
                )
            ).scalar_one_or_none()
            plan_name = subscription.plan_name if subscription else None

        try:
            outcome = await notify_payment_method_expired(
                self.session,
                opportunity=opportunity,
                customer=customer,
                attempt=attempt,
                action_url=action_url,
                plan_name=plan_name,
            )
        except Exception as exc:
            log.warning("razorpay.messaging_failed", error=type(exc).__name__)
            return {"status": "ERROR", "error": type(exc).__name__}

        return {
            "status": str(outcome.status),
            "channel": str(outcome.channel) if outcome.channel else None,
            "delivered_externally": outcome.sent,
            "reason": outcome.reason,
        }
