"""§49 — raw model output is never trusted, and §48 — the AI is never load-bearing."""

import pytest

from app.ai.agent import AgentDecision, RecoveryAgent
from app.ai.llm_client import LLMResponse
from app.ai.validation import validate_ai_output
from app.domain.context import RecoveryContext
from app.domain.enums import DecisionSource, RecoveryAction, Scenario


def _context(scenario=Scenario.FAILED_PAYMENT, **overrides) -> RecoveryContext:
    from app.domain.enums import SCENARIO_ACTIONS

    base = dict(
        opportunity_ref="OPP0001",
        scenario=scenario,
        amount_at_risk=5000.0,
        failure={"category": "TEMPORARY", "code": "BANK_TIMEOUT", "is_retryable_class": True},
        customer={
            "customer_ref": "C0001", "segment": "HIGH_VALUE", "account_age_days": 412,
            "previous_transaction_count": 37, "previous_success_count": 34,
            "previous_failure_count": 3, "historical_success_rate": 0.92,
            "average_order_value": 4200.0, "lifetime_value": 155400.0,
        },
        recovery_history={"attempt_count": 0},
        allowed_actions=list(SCENARIO_ACTIONS[scenario]),
    )
    base.update(overrides)
    return RecoveryContext(**base)


VALID = {
    "action": "DELAYED_RETRY", "recovery_probability": 0.82, "confidence": 0.91,
    "reason": "Temporary bank timeout with a strong payment history.", "risk_level": "LOW",
}


def test_valid_output_is_accepted():
    result = validate_ai_output(VALID, _context())
    assert result.ok
    assert result.decision.action is RecoveryAction.DELAYED_RETRY


def test_json_string_is_parsed():
    import json

    assert validate_ai_output(json.dumps(VALID), _context()).ok


@pytest.mark.parametrize(
    "bad,expect",
    [
        ("not json at all", "not valid JSON"),
        ('{"action": "DELAYED_RETRY"', "not valid JSON"),
        ("[1, 2, 3]", "expected object"),
    ],
)
def test_malformed_output_is_rejected(bad, expect):
    result = validate_ai_output(bad, _context())
    assert not result.ok
    assert any(expect in e for e in result.errors)


def test_invented_action_is_rejected():
    result = validate_ai_output({**VALID, "action": "REFUND_EVERYTHING"}, _context())
    assert not result.ok
    assert any("unknown action" in e for e in result.errors)


def test_action_from_another_scenario_is_rejected():
    """A checkout opportunity may not be answered with a subscription action."""
    result = validate_ai_output(
        {**VALID, "action": "RETRY_SUBSCRIPTION"}, _context(Scenario.CHECKOUT_ABANDONMENT)
    )
    assert not result.ok
    assert any("not valid for scenario" in e for e in result.errors)


@pytest.mark.parametrize("value", [1.4, -0.2, "high", None, True])
def test_out_of_range_probability_is_rejected(value):
    result = validate_ai_output({**VALID, "recovery_probability": value}, _context())
    assert not result.ok


def test_invalid_risk_level_is_rejected():
    assert not validate_ai_output({**VALID, "risk_level": "CATASTROPHIC"}, _context()).ok


@pytest.mark.parametrize("reason", ["", "   ", None, "x" * 401])
def test_bad_reason_is_rejected(reason):
    assert not validate_ai_output({**VALID, "reason": reason}, _context()).ok


# --- fallback behaviour (§48) ---------------------------------------------


class _StubClient:
    model = "stub-model"

    def __init__(self, response: LLMResponse):
        self._response = response
        self.calls = 0

    async def decide(self, _context):
        self.calls += 1
        return self._response

    async def aclose(self):
        pass


async def test_agent_falls_back_when_llm_times_out():
    client = _StubClient(LLMResponse(None, 20_000, "stub-model", error="APITimeoutError"))
    agent = RecoveryAgent(mode="llm", client=client)

    decision = await agent.decide(_context())

    assert isinstance(decision, AgentDecision)
    assert decision.source is DecisionSource.HEURISTIC_FALLBACK
    assert decision.output.action in set(RecoveryAction)
    assert decision.validation_errors


async def test_agent_falls_back_when_llm_returns_garbage():
    client = _StubClient(LLMResponse({"action": "NONSENSE"}, 120, "stub-model"))
    agent = RecoveryAgent(mode="llm", client=client)

    decision = await agent.decide(_context())

    assert decision.source is DecisionSource.HEURISTIC_FALLBACK
    assert any("unknown action" in e for e in decision.validation_errors)


async def test_agent_uses_valid_llm_output():
    client = _StubClient(LLMResponse(VALID, 300, "stub-model"))
    agent = RecoveryAgent(mode="llm", client=client)

    decision = await agent.decide(_context())

    assert decision.source is DecisionSource.LLM
    assert decision.output.action is RecoveryAction.DELAYED_RETRY
    assert decision.output.recovery_probability == 0.82


async def test_repeated_context_is_served_from_cache():
    client = _StubClient(LLMResponse(VALID, 300, "stub-model"))
    agent = RecoveryAgent(mode="auto", client=client)

    first = await agent.decide(_context())
    second = await agent.decide(_context())

    assert first.source is DecisionSource.LLM
    assert second.source is DecisionSource.LLM_CACHED
    assert client.calls == 1, "identical context must not be re-asked"


async def test_budget_exhaustion_degrades_to_heuristic_not_failure():
    client = _StubClient(LLMResponse(VALID, 300, "stub-model"))
    agent = RecoveryAgent(mode="auto", client=client, llm_budget=1)

    await agent.decide(_context())
    beyond = await agent.decide(_context(amount_at_risk=19999.0))

    assert beyond.source is DecisionSource.HEURISTIC_FALLBACK
    assert client.calls == 1


async def test_heuristic_mode_never_calls_the_model():
    client = _StubClient(LLMResponse(VALID, 300, "stub-model"))
    agent = RecoveryAgent(mode="heuristic", client=client)

    decision = await agent.decide(_context())

    assert decision.source is DecisionSource.HEURISTIC
    assert client.calls == 0
