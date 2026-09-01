"""Liveness and readiness. Reports capability booleans only — never secret values."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.envelope import ok
from app.core.config import settings
from app.db.session import SessionLocal

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    db_ok, db_error = True, None
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on environment
        db_ok, db_error = False, type(exc).__name__

    return ok(
        {
            "status": "ok" if db_ok else "degraded",
            "environment": settings.environment,
            "database": {"connected": db_ok, "error": db_error},
            "ai": {
                "mode": settings.ai_mode,
                "llm_available": settings.llm_available,
                "model": settings.ai_model if settings.llm_available else None,
            },
            "razorpay": {
                "configured": settings.razorpay_configured,
                "webhook_configured": settings.razorpay_webhook_configured,
                "enabled": settings.razorpay_enabled,
            },
        }
    )
