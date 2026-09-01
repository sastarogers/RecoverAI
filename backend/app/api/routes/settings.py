"""Policy configuration (§51). Reveals thresholds, never secrets."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.envelope import ok
from app.core.config import settings

router = APIRouter(prefix="/settings", tags=["settings"])


class PolicyUpdate(BaseModel):
    max_attempts: int | None = Field(None, ge=1, le=10)
    max_notifications: int | None = Field(None, ge=0, le=10)
    cooldown_minutes: int | None = Field(None, ge=0, le=1440)
    max_discount_minor: int | None = Field(None, ge=0)


def _current() -> dict:
    return {
        "max_attempts": settings.policy_max_attempts,
        "max_notifications": settings.policy_max_notifications,
        "cooldown_minutes": settings.policy_cooldown_minutes,
        "max_discount_minor": settings.policy_max_discount_minor,
        "opportunity_ttl_hours": settings.policy_opportunity_ttl_hours,
    }


@router.get("/policy")
async def get_policy() -> dict:
    return ok(
        {
            "policy": _current(),
            "ai": {
                "mode": settings.ai_mode,
                "model": settings.ai_model if settings.llm_available else None,
                "llm_available": settings.llm_available,
                "llm_budget_per_run": settings.ai_llm_budget_per_run,
                "max_concurrency": settings.ai_max_concurrency,
                "timeout_seconds": settings.ai_timeout_seconds,
            },
            "rules": [
                {"id": "P01_ALREADY_RECOVERED", "description": "Never act on already-recovered revenue"},
                {"id": "P02_TERMINAL", "description": "Never act on a closed opportunity"},
                {"id": "P03_MAX_ATTEMPTS", "description": "Cap recovery attempts per opportunity"},
                {"id": "P04_NON_RETRYABLE_FAILURE", "description": "Never retry a permanently failed instrument"},
                {"id": "P05_REPEATED_RETRY", "description": "Never repeat a retry that already failed"},
                {"id": "P06_NOTIFICATION_FATIGUE", "description": "Cap customer-facing messages"},
                {"id": "P07_ACTION_SCENARIO_MISMATCH", "description": "Action must be valid for the scenario"},
                {"id": "P08_DISCOUNT_REQUIRES_APPROVAL", "description": "Large discounts need approval"},
                {"id": "P09_COOLDOWN", "description": "Enforce a gap between attempts"},
                {"id": "P12_LOW_CONFIDENCE_HIGH_RISK", "description": "High-risk actions need confidence"},
            ],
        }
    )


@router.put("/policy")
async def update_policy(update: PolicyUpdate) -> dict:
    """Update thresholds for this process.

    Deliberately in-memory: the values are stamped into every simulation run's config,
    so a run's results always carry the policy that produced them.
    """
    for field, value in update.model_dump(exclude_none=True).items():
        setattr(settings, f"policy_{field}", value)
    return ok({"policy": _current()})
