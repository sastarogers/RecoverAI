"""Domain errors mapped to stable API error codes."""

from __future__ import annotations


class RecoverAIError(Exception):
    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(RecoverAIError):
    code = "NOT_FOUND"
    status_code = 404


class ValidationError(RecoverAIError):
    code = "VALIDATION_ERROR"
    status_code = 422


class IllegalStateTransition(RecoverAIError):
    code = "ILLEGAL_STATE_TRANSITION"
    status_code = 409


class DuplicateSettlementError(RecoverAIError):
    code = "DUPLICATE_SETTLEMENT"
    status_code = 409


class PolicyViolation(RecoverAIError):
    code = "POLICY_VIOLATION"
    status_code = 403


class WebhookSignatureError(RecoverAIError):
    code = "INVALID_SIGNATURE"
    status_code = 403


class IntegrationError(RecoverAIError):
    code = "INTEGRATION_ERROR"
    status_code = 502


class AIDecisionError(RecoverAIError):
    code = "AI_DECISION_ERROR"
    status_code = 502
