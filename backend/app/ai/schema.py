"""The AI decision contract (§20).

One schema serves all three scenarios. The model is told which actions are legal for
the opportunity in front of it; the policy engine independently re-checks that, so a
model that ignores the instruction changes nothing about what actually executes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import RecoveryAction, RiskLevel

MAX_REASON_CHARS = 400


class AIDecisionOutput(BaseModel):
    """Exactly what the model is asked to return."""

    model_config = ConfigDict(extra="forbid")

    action: RecoveryAction
    recovery_probability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)
    risk_level: RiskLevel


def decision_json_schema(allowed_actions: list[RecoveryAction]) -> dict:
    """Strict JSON schema for `output_config.format`, narrowed per scenario."""
    return {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [str(a) for a in allowed_actions],
                    "description": "The single recovery action to take now.",
                },
                "recovery_probability": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Estimated chance this action recovers the revenue.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "How confident you are in this recommendation.",
                },
                "reason": {
                    "type": "string",
                    "maxLength": MAX_REASON_CHARS,
                    "description": (
                        "One or two sentences citing the specific signals that drove "
                        "the choice. No step-by-step reasoning."
                    ),
                },
                "risk_level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
            },
            "required": [
                "action", "recovery_probability", "confidence", "reason", "risk_level",
            ],
            "additionalProperties": False,
        },
    }
