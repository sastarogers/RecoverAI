"""Canonical vocabulary of the RecoverAI domain.

These enums are the contract shared by the normalizer, AI agent, policy engine,
executor, ledger and API. Nothing downstream invents its own strings.
"""

from __future__ import annotations

from enum import StrEnum


class Source(StrEnum):
    RAZORPAY = "RAZORPAY"
    SIMULATOR = "SIMULATOR"


class Scenario(StrEnum):
    FAILED_PAYMENT = "FAILED_PAYMENT"
    CHECKOUT_ABANDONMENT = "CHECKOUT_ABANDONMENT"
    FAILED_SUBSCRIPTION = "FAILED_SUBSCRIPTION"


class EventType(StrEnum):
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_AUTHORIZED = "PAYMENT_AUTHORIZED"
    PAYMENT_CAPTURED = "PAYMENT_CAPTURED"
    ORDER_PAID = "ORDER_PAID"
    CHECKOUT_STARTED = "CHECKOUT_STARTED"
    CHECKOUT_ABANDONED = "CHECKOUT_ABANDONED"
    CHECKOUT_COMPLETED = "CHECKOUT_COMPLETED"
    SUBSCRIPTION_PAYMENT_FAILED = "SUBSCRIPTION_PAYMENT_FAILED"
    SUBSCRIPTION_RENEWED = "SUBSCRIPTION_RENEWED"
    SUBSCRIPTION_HALTED = "SUBSCRIPTION_HALTED"
    PAYMENT_METHOD_UPDATED = "PAYMENT_METHOD_UPDATED"


class FailureCategory(StrEnum):
    TEMPORARY = "TEMPORARY"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    BANK_DECLINE = "BANK_DECLINE"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    INVALID_PAYMENT_DETAILS = "INVALID_PAYMENT_DETAILS"
    EXPIRED_CARD = "EXPIRED_CARD"
    CUSTOMER_ACTION_REQUIRED = "CUSTOMER_ACTION_REQUIRED"
    PERMANENT = "PERMANENT"
    UNKNOWN = "UNKNOWN"
    ABANDONED = "ABANDONED"  # checkout scenario: no gateway failure exists


#: Categories that must never be blindly retried (policy rule P04).
NON_RETRYABLE_CATEGORIES: frozenset[FailureCategory] = frozenset(
    {
        FailureCategory.PERMANENT,
        FailureCategory.EXPIRED_CARD,
        FailureCategory.INVALID_PAYMENT_DETAILS,
    }
)

#: Categories where a *later* retry is plausible but an immediate one is not.
DELAY_ONLY_CATEGORIES: frozenset[FailureCategory] = frozenset(
    {FailureCategory.INSUFFICIENT_FUNDS, FailureCategory.BANK_DECLINE}
)


class PaymentMethod(StrEnum):
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMI = "emi"


CARD_METHODS: frozenset[PaymentMethod] = frozenset({PaymentMethod.CARD, PaymentMethod.EMI})


class CustomerSegment(StrEnum):
    HIGH_VALUE = "HIGH_VALUE"
    REGULAR = "REGULAR"
    NEW = "NEW"
    AT_RISK = "AT_RISK"
    LOW_ENGAGEMENT = "LOW_ENGAGEMENT"


class RecoveryAction(StrEnum):
    IMMEDIATE_RETRY = "IMMEDIATE_RETRY"
    DELAYED_RETRY = "DELAYED_RETRY"
    PAYMENT_LINK = "PAYMENT_LINK"
    CHECKOUT_RESUME = "CHECKOUT_RESUME"
    REMINDER = "REMINDER"
    DISCOUNT_INCENTIVE = "DISCOUNT_INCENTIVE"
    RETRY_SUBSCRIPTION = "RETRY_SUBSCRIPTION"
    PAYMENT_UPDATE_REQUEST = "PAYMENT_UPDATE_REQUEST"
    ALTERNATE_PAYMENT_METHOD = "ALTERNATE_PAYMENT_METHOD"
    CUSTOMER_NOTIFICATION = "CUSTOMER_NOTIFICATION"
    GRACE_PERIOD = "GRACE_PERIOD"
    STOP = "STOP"


#: Which actions each scenario is allowed to use. The AI is told this, and the
#: policy engine enforces it independently (rule P07) — the AI cannot widen it.
SCENARIO_ACTIONS: dict[Scenario, tuple[RecoveryAction, ...]] = {
    Scenario.FAILED_PAYMENT: (
        RecoveryAction.IMMEDIATE_RETRY,
        RecoveryAction.DELAYED_RETRY,
        RecoveryAction.PAYMENT_LINK,
        RecoveryAction.ALTERNATE_PAYMENT_METHOD,
        RecoveryAction.CUSTOMER_NOTIFICATION,
        RecoveryAction.STOP,
    ),
    Scenario.CHECKOUT_ABANDONMENT: (
        RecoveryAction.REMINDER,
        RecoveryAction.PAYMENT_LINK,
        RecoveryAction.CHECKOUT_RESUME,
        RecoveryAction.DISCOUNT_INCENTIVE,
        RecoveryAction.ALTERNATE_PAYMENT_METHOD,
        RecoveryAction.CUSTOMER_NOTIFICATION,
        RecoveryAction.STOP,
    ),
    Scenario.FAILED_SUBSCRIPTION: (
        RecoveryAction.RETRY_SUBSCRIPTION,
        RecoveryAction.DELAYED_RETRY,
        RecoveryAction.PAYMENT_UPDATE_REQUEST,
        RecoveryAction.PAYMENT_LINK,
        RecoveryAction.CUSTOMER_NOTIFICATION,
        RecoveryAction.ALTERNATE_PAYMENT_METHOD,
        RecoveryAction.GRACE_PERIOD,
        RecoveryAction.STOP,
    ),
}

#: Actions that re-attempt the original charge (subject to non-retryable rules).
RETRY_ACTIONS: frozenset[RecoveryAction] = frozenset(
    {
        RecoveryAction.IMMEDIATE_RETRY,
        RecoveryAction.DELAYED_RETRY,
        RecoveryAction.RETRY_SUBSCRIPTION,
    }
)

#: Actions that put a message in front of the customer (notification-fatigue rule P06).
NOTIFYING_ACTIONS: frozenset[RecoveryAction] = frozenset(
    {
        RecoveryAction.CUSTOMER_NOTIFICATION,
        RecoveryAction.REMINDER,
        RecoveryAction.PAYMENT_LINK,
        RecoveryAction.CHECKOUT_RESUME,
        RecoveryAction.DISCOUNT_INCENTIVE,
        RecoveryAction.PAYMENT_UPDATE_REQUEST,
        RecoveryAction.ALTERNATE_PAYMENT_METHOD,
    }
)

#: The only action that does not attempt to move money at all. GRACE_PERIOD is a real
#: dunning strategy — customers do resolve funds during a grace window — so it carries a
#: genuine success probability rather than being treated as a no-op.
PASSIVE_ACTIONS: frozenset[RecoveryAction] = frozenset({RecoveryAction.STOP})


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class OpportunityStatus(StrEnum):
    DETECTED = "DETECTED"
    ANALYZING = "ANALYZING"
    RECOMMENDED = "RECOMMENDED"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    EXECUTING = "EXECUTING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RECOVERED = "RECOVERED"
    EXHAUSTED = "EXHAUSTED"
    EXPIRED = "EXPIRED"


class PolicyVerdict(StrEnum):
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    MODIFIED = "MODIFIED"


class DecisionSource(StrEnum):
    LLM = "LLM"
    LLM_CACHED = "LLM_CACHED"
    HEURISTIC = "HEURISTIC"
    HEURISTIC_FALLBACK = "HEURISTIC_FALLBACK"


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    SKIPPED = "SKIPPED"


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PENDING = "PENDING"
    NO_RESPONSE = "NO_RESPONSE"


class EvidenceType(StrEnum):
    SIMULATED_GROUND_TRUTH = "SIMULATED_GROUND_TRUTH"
    RAZORPAY_WEBHOOK = "RAZORPAY_WEBHOOK"
    MANUAL = "MANUAL"


class ExecutorKind(StrEnum):
    SIMULATOR = "SIMULATOR"
    RAZORPAY = "RAZORPAY"
    NOOP = "NOOP"


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    ATTEMPTED = "ATTEMPTED"
    PAID = "PAID"
    FAILED = "FAILED"


class CheckoutStatus(StrEnum):
    STARTED = "STARTED"
    ABANDONED = "ABANDONED"
    COMPLETED = "COMPLETED"
    RECOVERED = "RECOVERED"


class SubscriptionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    GRACE_PERIOD = "GRACE_PERIOD"
    CANCELLED = "CANCELLED"


class BillingCycle(StrEnum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


class SubscriptionEventType(StrEnum):
    RENEWAL_ATTEMPTED = "RENEWAL_ATTEMPTED"
    RENEWAL_SUCCESS = "RENEWAL_SUCCESS"
    RENEWAL_FAILED = "RENEWAL_FAILED"
    PAYMENT_METHOD_UPDATED = "PAYMENT_METHOD_UPDATED"
    GRACE_GRANTED = "GRACE_GRANTED"
    HALTED = "HALTED"


class LedgerEntryType(StrEnum):
    RECOVERED = "RECOVERED"


class SimulationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BaselineStrategy(StrEnum):
    NO_RECOVERY = "NO_RECOVERY"
    ALWAYS_RETRY = "ALWAYS_RETRY"
    FIXED_RETRY = "FIXED_RETRY"
    RECOVERAI = "RECOVERAI"


class WebhookStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    DUPLICATE = "DUPLICATE"
    INVALID = "INVALID"
    FAILED = "FAILED"


class Actor(StrEnum):
    SYSTEM = "SYSTEM"
    AI = "AI"
    POLICY = "POLICY"
    EXECUTOR = "EXECUTOR"
    WEBHOOK = "WEBHOOK"
    USER = "USER"
    SIMULATOR = "SIMULATOR"
    LEDGER = "LEDGER"
