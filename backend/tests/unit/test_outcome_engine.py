"""Outcome draws must be reproducible and must respect hidden truth."""

from app.domain.enums import Outcome, RecoveryAction
from app.simulation.outcome import draw_outcome, true_probability_for

PROBS = {"DELAYED_RETRY": 0.82, "IMMEDIATE_RETRY": 0.45, "PAYMENT_LINK": 0.61, "STOP": 0.0}


def _draw(seed=42, ref="OPP0001", n=1, action=RecoveryAction.DELAYED_RETRY):
    return draw_outcome(
        seed=seed, opportunity_ref=ref, attempt_number=n, action=action,
        action_success_probs=PROBS,
    )


def test_same_seed_and_scope_reproduces_the_same_outcome():
    assert _draw().outcome == _draw().outcome
    assert _draw().roll == _draw().roll


def test_different_opportunities_draw_independently():
    rolls = {_draw(ref=f"OPP{i:04d}").roll for i in range(20)}
    assert len(rolls) == 20, "each opportunity must have its own stream"


def test_outcome_is_independent_of_evaluation_order():
    """Concurrency safety: draws do not depend on how many draws preceded them."""
    forward = [_draw(ref=f"OPP{i:04d}").roll for i in range(10)]
    backward = [_draw(ref=f"OPP{i:04d}").roll for i in reversed(range(10))][::-1]
    assert forward == backward


def test_stop_never_succeeds():
    result = _draw(action=RecoveryAction.STOP)
    assert result.outcome is Outcome.NO_RESPONSE
    assert result.true_probability == 0.0


def test_unrecoverable_opportunity_never_succeeds():
    dead = {"DELAYED_RETRY": 0.0, "PAYMENT_LINK": 0.0}
    for i in range(50):
        result = draw_outcome(
            seed=7, opportunity_ref=f"OPP{i:04d}", attempt_number=1,
            action=RecoveryAction.DELAYED_RETRY, action_success_probs=dead,
        )
        assert result.outcome is Outcome.FAILURE


def test_later_attempts_are_harder():
    assert (
        true_probability_for(PROBS, RecoveryAction.DELAYED_RETRY, 1)
        > true_probability_for(PROBS, RecoveryAction.DELAYED_RETRY, 2)
        > true_probability_for(PROBS, RecoveryAction.DELAYED_RETRY, 3)
    )


def test_empirical_rate_tracks_true_probability():
    successes = sum(
        1
        for i in range(2000)
        if draw_outcome(
            seed=99, opportunity_ref=f"OPP{i:05d}", attempt_number=1,
            action=RecoveryAction.DELAYED_RETRY, action_success_probs=PROBS,
        ).outcome
        is Outcome.SUCCESS
    )
    assert 0.79 < successes / 2000 < 0.85, "draws must honour the stated probability"
