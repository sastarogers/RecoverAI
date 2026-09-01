"""Simulated outcome engine (§19).

This is the *only* place a stored ground-truth probability is read, and it runs strictly
after the AI decision and the policy verdict have been persisted. The draw is derived
from (seed, opportunity_ref, attempt_number) rather than from a shared global stream,
so a run reproduces exactly even though AI calls resolve concurrently and out of order.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.rng import clamp, derive_rng
from app.domain.enums import Outcome, RecoveryAction
from app.simulation.ground_truth import ATTEMPT_DECAY


@dataclass(slots=True)
class OutcomeDraw:
    outcome: Outcome
    #: The true probability used — recorded for calibration analysis, never shown to the AI.
    true_probability: float
    roll: float
    action: str


def reproducibility_key(opportunity) -> str:
    """The identity to seed randomness on.

    Prefers the run-independent `simulation_key`; falls back to the display ref for
    opportunities that did not come from the generator (live Razorpay events), where
    cross-run reproducibility is not a meaningful concept anyway.
    """
    return getattr(opportunity, "simulation_key", None) or opportunity.opportunity_ref


def _decay(attempt_number: int) -> float:
    idx = max(0, attempt_number - 1)
    return ATTEMPT_DECAY[min(idx, len(ATTEMPT_DECAY) - 1)]


def true_probability_for(
    action_success_probs: dict[str, float], action: RecoveryAction, attempt_number: int
) -> float:
    """Stored probabilities describe attempt 1; later attempts decay from there."""
    if action == RecoveryAction.STOP:
        return 0.0
    base = float(action_success_probs.get(str(action), 0.0))
    return clamp(base * (_decay(attempt_number) / _decay(1)), 0.0, 0.97)


def draw_outcome(
    *,
    seed: int,
    opportunity_ref: str,
    attempt_number: int,
    action: RecoveryAction,
    action_success_probs: dict[str, float],
) -> OutcomeDraw:
    """Resolve one recovery attempt against hidden truth."""
    p = true_probability_for(action_success_probs, action, attempt_number)
    if action == RecoveryAction.STOP:
        # Stopping is a decision, not an attempt: it never produces a success or a failure.
        return OutcomeDraw(Outcome.NO_RESPONSE, 0.0, 0.0, str(action))

    rng = derive_rng(seed, "outcome", opportunity_ref, attempt_number)
    roll = rng.random()
    outcome = Outcome.SUCCESS if roll < p else Outcome.FAILURE
    return OutcomeDraw(outcome, round(p, 4), round(roll, 6), str(action))
