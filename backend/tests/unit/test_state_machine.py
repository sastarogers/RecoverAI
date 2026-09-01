"""The state machine is the structural half of the anti-double-count guarantee."""

import pytest

from app.core.errors import IllegalStateTransition
from app.domain.enums import OpportunityStatus as S
from app.domain.state_machine import (
    TERMINAL_STATES,
    can_transition,
    is_terminal,
    transition,
)
from tests.factories import make_customer, make_opportunity


def test_happy_path_is_legal():
    path = [S.DETECTED, S.ANALYZING, S.RECOMMENDED, S.APPROVED, S.EXECUTING, S.SUCCESS, S.RECOVERED]
    for current, target in zip(path, path[1:], strict=False):
        assert can_transition(current, target), f"{current} -> {target} should be legal"


def test_blocked_path_is_legal():
    assert can_transition(S.RECOMMENDED, S.BLOCKED)
    assert can_transition(S.BLOCKED, S.ANALYZING)  # retry if attempts remain
    assert can_transition(S.BLOCKED, S.EXHAUSTED)


def test_failed_attempt_can_be_reanalyzed():
    assert can_transition(S.EXECUTING, S.FAILED)
    assert can_transition(S.FAILED, S.ANALYZING)
    assert can_transition(S.FAILED, S.EXHAUSTED)


def test_recovered_is_absorbing():
    """Nothing may leave RECOVERED — the money is settled exactly once."""
    for target in S:
        assert not can_transition(S.RECOVERED, target), f"RECOVERED must not reach {target}"


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_terminal_states_have_no_exits(state):
    assert is_terminal(state)
    assert all(not can_transition(state, t) for t in S)


def test_ai_cannot_skip_policy():
    """There is no edge that reaches EXECUTING without passing through APPROVED."""
    assert not can_transition(S.RECOMMENDED, S.EXECUTING)
    assert not can_transition(S.ANALYZING, S.EXECUTING)
    assert not can_transition(S.DETECTED, S.EXECUTING)


def test_cannot_recover_without_success():
    assert not can_transition(S.EXECUTING, S.RECOVERED)
    assert not can_transition(S.FAILED, S.RECOVERED)
    assert not can_transition(S.APPROVED, S.RECOVERED)


def test_transition_raises_on_illegal_move():
    cust = make_customer()
    opp = make_opportunity(cust)
    opp.status = S.DETECTED
    transition(opp, S.ANALYZING)
    assert opp.status == S.ANALYZING
    with pytest.raises(IllegalStateTransition) as exc:
        transition(opp, S.RECOVERED)
    assert "OPP0001" in str(exc.value.details["opportunity_ref"])
