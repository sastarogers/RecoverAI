"""§43 — demo scenarios are scripted, so they must be repeatable.

Ground truth here is the real model, not rigged odds; what is pinned is the *key* it is
computed from. Keyed on the opportunity ref (which increments per click) the same button
produced a different outcome each press, which is not something to run in front of an
audience.
"""

from __future__ import annotations

import pytest

from app.domain.enums import FailureCategory, RecoveryAction, Scenario
from app.simulation.ground_truth import compute_ground_truth
from app.simulation.outcome import draw_outcome, reproducibility_key

DEMO_SEED = 777

CASES = [
    ("demo:failed-payment", Scenario.FAILED_PAYMENT, FailureCategory.TEMPORARY, 500_000),
    ("demo:checkout-abandonment", Scenario.CHECKOUT_ABANDONMENT, FailureCategory.ABANDONED, 700_000),
    ("demo:subscription-failure", Scenario.FAILED_SUBSCRIPTION, FailureCategory.EXPIRED_CARD, 99_900),
]


@pytest.mark.parametrize("key,scenario,category,amount", CASES)
def test_demo_ground_truth_is_stable_across_clicks(key, scenario, category, amount):
    runs = [
        compute_ground_truth(
            seed=DEMO_SEED, opportunity_ref=key, scenario=scenario,
            failure_category=category, amount_minor=amount,
            customer_reliability=0.8, price_sensitivity=0.5, unrecoverable_rate=0.1,
        )
        for _ in range(5)
    ]
    first = runs[0]
    for other in runs[1:]:
        assert other.action_success_probs == first.action_success_probs
        assert other.optimal_action == first.optimal_action
        assert other.is_recoverable == first.is_recoverable


@pytest.mark.parametrize("key,scenario,category,amount", CASES)
def test_demo_outcome_is_stable_across_clicks(key, scenario, category, amount):
    truth = compute_ground_truth(
        seed=DEMO_SEED, opportunity_ref=key, scenario=scenario,
        failure_category=category, amount_minor=amount,
        customer_reliability=0.8, price_sensitivity=0.5, unrecoverable_rate=0.1,
    )
    action = RecoveryAction(truth.optimal_action)
    draws = {
        draw_outcome(
            seed=DEMO_SEED, opportunity_ref=key, attempt_number=1,
            action=action, action_success_probs=truth.action_success_probs,
        ).outcome
        for _ in range(8)
    }
    assert len(draws) == 1, "the same demo scenario resolved differently between clicks"


def test_incrementing_refs_would_not_be_stable():
    """Guards the reason the fix exists — keying on a per-click ref reintroduces the flap."""
    outcomes = {
        draw_outcome(
            seed=DEMO_SEED, opportunity_ref=f"OPP{n:04d}", attempt_number=1,
            action=RecoveryAction.DELAYED_RETRY,
            action_success_probs={"DELAYED_RETRY": 0.6},
        ).outcome
        for n in range(1, 40)
    }
    assert len(outcomes) > 1


def test_opportunity_simulation_key_drives_the_draw():
    """The demo stamps `simulation_key`, and the outcome engine must honour it."""

    class _Opp:
        simulation_key = "demo:failed-payment"
        opportunity_ref = "OPP9999"

    assert reproducibility_key(_Opp()) == "demo:failed-payment"
