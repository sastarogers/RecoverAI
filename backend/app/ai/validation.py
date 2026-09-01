"""AI output validation gate (§49).

Raw model output is never trusted. Every field is re-checked here — including the
scenario/action compatibility the prompt already asked for — before anything reaches
the policy engine. A rejected output is not an error condition: it is recorded and the
deterministic fallback takes over (§48).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.ai.schema import MAX_REASON_CHARS, AIDecisionOutput
from app.domain.context import RecoveryContext
from app.domain.enums import SCENARIO_ACTIONS, RecoveryAction, RiskLevel


@dataclass(slots=True)
class ValidationResult:
    decision: AIDecisionOutput | None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.decision is not None and not self.errors


def validate_ai_output(raw: str | dict, context: RecoveryContext) -> ValidationResult:
    """Parse and validate a model response against the contract and the context."""
    errors: list[str] = []

    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            return ValidationResult(None, [f"output is not valid JSON: {exc}"])

    if not isinstance(payload, dict):
        return ValidationResult(None, [f"output is {type(payload).__name__}, expected object"])

    # --- action ---
    raw_action = payload.get("action")
    action: RecoveryAction | None = None
    try:
        action = RecoveryAction(str(raw_action))
    except ValueError:
        errors.append(f"unknown action {raw_action!r}")

    if action is not None:
        allowed = set(SCENARIO_ACTIONS.get(context.scenario, ()))
        if action not in allowed:
            errors.append(
                f"action {action} is not valid for scenario {context.scenario}"
            )
        elif action not in set(context.allowed_actions):
            errors.append(f"action {action} was not offered for this opportunity")

    # --- numeric ranges ---
    for name in ("recovery_probability", "confidence"):
        value = payload.get(name)
        if not isinstance(value, int | float) or isinstance(value, bool):
            errors.append(f"{name} must be a number, got {type(value).__name__}")
        elif not 0.0 <= float(value) <= 1.0:
            errors.append(f"{name} must be within [0, 1], got {value}")

    # --- risk level ---
    raw_risk = payload.get("risk_level")
    try:
        RiskLevel(str(raw_risk))
    except ValueError:
        errors.append(f"invalid risk_level {raw_risk!r}")

    # --- reason ---
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append("reason is required")
    elif len(reason) > MAX_REASON_CHARS:
        errors.append(f"reason exceeds {MAX_REASON_CHARS} characters")

    if errors:
        return ValidationResult(None, errors)

    try:
        decision = AIDecisionOutput.model_validate(payload)
    except Exception as exc:  # schema drift the field checks did not catch
        return ValidationResult(None, [f"schema validation failed: {exc}"])

    return ValidationResult(decision, [])
