"""§18 / RULE 7 — the AI must never be able to see the simulator's hidden truth.

This is a correctness property, not a convention, so it is asserted two ways:
  1. statically, over the import graph of every module the AI decision path touches;
  2. dynamically, over the serialized context that actually reaches the model.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

#: Modules that participate in producing a decision, before any outcome exists.
DECISION_PATH_PACKAGES = ("context", "ai", "policy", "domain")

FORBIDDEN_IMPORTS = ("app.simulation.ground_truth", "app.simulation.outcome")


def _module_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for pkg in DECISION_PATH_PACKAGES:
        files.extend(sorted((APP / pkg).rglob("*.py")))
    return files


def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_decision_path_never_imports_ground_truth(path: pathlib.Path):
    leaked = _imports_of(path) & set(FORBIDDEN_IMPORTS)
    assert not leaked, f"{path.relative_to(APP)} imports hidden ground truth: {leaked}"


def test_context_model_forbids_ground_truth_fields():
    """RecoveryContext rejects extras, so a leak cannot be smuggled in as a stray key."""
    from app.domain.context import RecoveryContext

    assert RecoveryContext.model_config.get("extra") == "forbid"


def test_serialized_context_contains_no_forbidden_keys():
    from app.domain.context import FORBIDDEN_CONTEXT_KEYS, RecoveryContext
    from app.domain.enums import RecoveryAction, Scenario

    ctx = RecoveryContext(
        opportunity_ref="OPP0001",
        scenario=Scenario.FAILED_PAYMENT,
        amount_at_risk=5000.0,
        failure={"category": "TEMPORARY", "code": "BANK_TIMEOUT", "is_retryable_class": True},
        customer={
            "customer_ref": "C0001", "segment": "HIGH_VALUE", "account_age_days": 412,
            "previous_transaction_count": 37, "previous_success_count": 34,
            "previous_failure_count": 3, "historical_success_rate": 0.92,
            "average_order_value": 4200.0, "lifetime_value": 155400.0,
        },
        recovery_history={"attempt_count": 0},
        allowed_actions=[RecoveryAction.DELAYED_RETRY, RecoveryAction.STOP],
    )
    serialized = ctx.to_prompt_dict()
    flat = _flatten_keys(serialized)
    leaked = flat & FORBIDDEN_CONTEXT_KEYS
    assert not leaked, f"context leaked hidden fields: {leaked}"


def _flatten_keys(obj, acc: set[str] | None = None) -> set[str]:
    acc = acc if acc is not None else set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _flatten_keys(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _flatten_keys(item, acc)
    return acc
