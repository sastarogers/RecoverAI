"""Recovery executor interface (§23).

One interface, two backends. The simulator resolves an attempt against hidden ground
truth; Razorpay creates a real Test Mode artifact and then *waits* — a Razorpay-sourced
recovery is only ever confirmed by an inbound webhook, never by writing a row.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.db.models import RecoveryAttempt, RecoveryOpportunity
from app.domain.enums import EvidenceType, ExecutorKind, Outcome, RecoveryAction


@dataclass(slots=True)
class ExecutionResult:
    """What executing one recovery action produced."""

    executed: bool
    #: PENDING means "the world has been asked; the answer will arrive later".
    outcome: Outcome
    realized_amount_minor: int = 0
    evidence_type: EvidenceType = EvidenceType.SIMULATED_GROUND_TRUTH
    evidence_ref: str | None = None
    external_ref: str | None = None
    attribution_token: str | None = None
    error: str | None = None
    details: dict = field(default_factory=dict)


class RecoveryExecutor(ABC):
    """Executes an approved recovery action."""

    kind: ExecutorKind

    @abstractmethod
    async def execute(
        self,
        *,
        opportunity: RecoveryOpportunity,
        attempt: RecoveryAttempt,
        action: RecoveryAction,
    ) -> ExecutionResult:
        """Carry out the action. Must not raise for ordinary failure — return a result."""


class NoopExecutor(RecoveryExecutor):
    """Used for STOP: the decision is recorded, nothing is executed."""

    kind = ExecutorKind.NOOP

    async def execute(self, *, opportunity, attempt, action) -> ExecutionResult:
        return ExecutionResult(
            executed=False,
            outcome=Outcome.NO_RESPONSE,
            details={"reason": "STOP requires no execution"},
        )
