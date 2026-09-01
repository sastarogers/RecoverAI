"""Recovery opportunity state machine.

Every status change goes through `transition()`. Illegal moves raise rather than
silently corrupting the ledger's invariants.

RECOVERED is terminal and absorbing — that is the state-machine half of the
"one opportunity contributes recovered revenue at most once" guarantee (RULE 3).
"""

from __future__ import annotations

from app.core.errors import IllegalStateTransition
from app.domain.enums import OpportunityStatus as S

#: Legal transitions. Anything not listed here is rejected.
TRANSITIONS: dict[S, frozenset[S]] = {
    S.DETECTED: frozenset({S.ANALYZING, S.EXPIRED}),
    S.ANALYZING: frozenset({S.RECOMMENDED, S.EXHAUSTED, S.EXPIRED}),
    S.RECOMMENDED: frozenset({S.APPROVED, S.BLOCKED, S.EXHAUSTED, S.EXPIRED}),
    S.APPROVED: frozenset({S.EXECUTING, S.BLOCKED, S.EXHAUSTED, S.EXPIRED}),
    # A blocked recommendation can be re-analyzed if attempts remain; otherwise exhausted.
    S.BLOCKED: frozenset({S.ANALYZING, S.EXHAUSTED, S.EXPIRED}),
    S.EXECUTING: frozenset({S.SUCCESS, S.FAILED, S.EXHAUSTED}),
    S.SUCCESS: frozenset({S.RECOVERED}),
    S.FAILED: frozenset({S.ANALYZING, S.EXHAUSTED, S.EXPIRED}),
    # Terminal states
    S.RECOVERED: frozenset(),
    S.EXHAUSTED: frozenset(),
    S.EXPIRED: frozenset(),
}

TERMINAL_STATES: frozenset[S] = frozenset({S.RECOVERED, S.EXHAUSTED, S.EXPIRED})

#: States in which no further recovery work should be scheduled.
CLOSED_STATES = TERMINAL_STATES


def can_transition(current: S, target: S) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def is_terminal(status: S) -> bool:
    return status in TERMINAL_STATES


def assert_transition(current: S, target: S, *, opportunity_ref: str | None = None) -> None:
    if not can_transition(current, target):
        raise IllegalStateTransition(
            f"Cannot move opportunity from {current} to {target}",
            details={
                "opportunity_ref": opportunity_ref,
                "current": str(current),
                "target": str(target),
                "allowed": sorted(str(s) for s in TRANSITIONS.get(current, frozenset())),
            },
        )


def transition(opportunity, target: S):
    """Apply a validated status change to an opportunity ORM object."""
    current = S(opportunity.status)
    assert_transition(current, target, opportunity_ref=getattr(opportunity, "opportunity_ref", None))
    opportunity.status = target
    return opportunity


#: Order used for funnel/pipeline visualisations on the dashboard.
PIPELINE_ORDER: tuple[S, ...] = (
    S.DETECTED,
    S.ANALYZING,
    S.RECOMMENDED,
    S.APPROVED,
    S.EXECUTING,
    S.SUCCESS,
    S.RECOVERED,
)
