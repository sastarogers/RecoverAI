"""Message copy.

Kept short and specific: an expired-card message that does not say what is wrong, which
subscription it concerns, or what to do next is just noise that spends the one contact
the policy engine allows.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.money import format_inr
from app.domain.enums import FailureCategory, MessageChannel, Scenario

TEMPLATE_PAYMENT_METHOD_UPDATE = "payment_method_update"

#: Plain-language reason per failure category, in the second person.
_REASON: dict[FailureCategory, str] = {
    FailureCategory.EXPIRED_CARD: "your saved card has expired",
    FailureCategory.INVALID_PAYMENT_DETAILS: "your saved payment details are no longer valid",
    FailureCategory.CUSTOMER_ACTION_REQUIRED: "your saved payment method is no longer authorised",
}

_SUBJECT: dict[Scenario, str] = {
    Scenario.FAILED_SUBSCRIPTION: "your {plan} subscription renewal",
    Scenario.FAILED_PAYMENT: "your recent payment",
    Scenario.CHECKOUT_ABANDONMENT: "your pending order",
}


@dataclass(slots=True)
class RenderedMessage:
    body: str
    template: str


def render_payment_method_update(
    *,
    channel: MessageChannel,
    merchant: str,
    customer_name: str | None,
    scenario: Scenario,
    category: FailureCategory,
    amount_minor: int,
    action_url: str | None,
    plan_name: str | None = None,
) -> RenderedMessage:
    """Compose the 'your payment method needs updating' message."""
    greeting = f"Hi {customer_name.split()[0]}," if customer_name else "Hi,"
    reason = _REASON.get(category, "your saved payment method needs attention")
    subject = _SUBJECT.get(scenario, "your recent payment").format(plan=plan_name or "your plan")
    amount = format_inr(amount_minor)

    lines = [
        greeting,
        f"We couldn't process {amount} for {subject} because {reason}.",
    ]
    if action_url:
        lines.append(f"Update your payment method here: {action_url}")
    else:
        lines.append("Please update your payment method to continue.")

    # SMS is charged per segment and has no formatting, so it stays terse; WhatsApp can
    # afford the sign-off that makes it read as a real merchant message.
    if channel is MessageChannel.WHATSAPP:
        lines.append(f"— {merchant}")
        lines.append("Reply STOP to opt out of these messages.")
    else:
        lines.append(f"- {merchant}. Reply STOP to opt out.")

    return RenderedMessage(body="\n".join(lines), template=TEMPLATE_PAYMENT_METHOD_UPDATE)
