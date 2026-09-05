"""Model registry — importing this module registers every table on Base.metadata."""

from app.db.base import Base
from app.db.models.counters import RefCounter
from app.db.models.entities import (
    CheckoutSession,
    Customer,
    Order,
    Payment,
    PaymentEvent,
    Subscription,
    SubscriptionEvent,
)
from app.db.models.governance import (
    AuditLog,
    BaselineResult,
    SimulationGroundTruth,
    SimulationRun,
    WebhookEvent,
)
from app.db.models.messaging import NotificationMessage
from app.db.models.recovery import (
    AIDecision,
    PolicyDecision,
    RecoveryAttempt,
    RecoveryLedger,
    RecoveryOpportunity,
    RecoveryOutcome,
)

__all__ = [
    "Base",
    "RefCounter",
    "Customer",
    "Order",
    "Payment",
    "PaymentEvent",
    "CheckoutSession",
    "Subscription",
    "SubscriptionEvent",
    "RecoveryOpportunity",
    "AIDecision",
    "PolicyDecision",
    "RecoveryAttempt",
    "RecoveryOutcome",
    "RecoveryLedger",
    "SimulationRun",
    "SimulationGroundTruth",
    "BaselineResult",
    "WebhookEvent",
    "AuditLog",
    "NotificationMessage",
]
