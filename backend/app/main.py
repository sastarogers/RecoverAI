"""RecoverAI API.

Composition root only: wiring, middleware, error mapping. No business logic here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import RecoverAIError
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine

log = get_logger("recoverai.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    log.info("recoverai.startup", environment=settings.environment, ai_mode=settings.ai_mode)
    yield
    await dispose_engine()


app = FastAPI(
    title="RecoverAI",
    version="0.1.0",
    description="AI-powered revenue recovery intelligence platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RecoverAIError)
async def domain_error_handler(_request: Request, exc: RecoverAIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


def _register_routes() -> None:
    from app.api.routes import (
        analytics,
        dashboard,
        demo,
        health,
        opportunities,
        razorpay,
        simulation,
    )
    from app.api.routes import (
        settings as settings_routes,
    )

    for module in (
        health,
        dashboard,
        opportunities,
        simulation,
        analytics,
        razorpay,
        demo,
        settings_routes,
    ):
        app.include_router(module.router, prefix=settings.api_prefix)


_register_routes()


@app.get("/")
async def root() -> dict:
    return {"name": "RecoverAI", "version": "0.1.0", "docs": "/docs"}
