"""Anthropic client wrapper: structured output, timeouts, bounded concurrency.

Kept deliberately thin. It returns raw payloads and never decides anything — parsing
and validation happen in `app.ai.validation`, so a malformed or hostile response is
handled by the same gate regardless of where it came from (§49).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

from app.ai.prompts import SYSTEM_PROMPT, build_user_message
from app.ai.schema import decision_json_schema
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.context import RecoveryContext

log = get_logger("recoverai.ai.llm")


@dataclass(slots=True)
class LLMResponse:
    payload: dict | str | None
    latency_ms: int
    model: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.payload is not None


class LLMUnavailable(RuntimeError):
    """Raised at construction when no API key is configured."""


class RecoveryLLMClient:
    """Async Anthropic client for recovery decisions."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        key = api_key or settings.anthropic_api_key
        if not key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not configured")

        from anthropic import AsyncAnthropic

        self.model = model or settings.ai_model
        self.timeout = timeout or settings.ai_timeout_seconds
        self._client = AsyncAnthropic(api_key=key, timeout=self.timeout, max_retries=1)
        # Bounded fan-out: a 2000-opportunity run must not open 2000 sockets.
        self._semaphore = asyncio.Semaphore(max_concurrency or settings.ai_max_concurrency)

    async def decide(self, context: RecoveryContext) -> LLMResponse:
        """Ask the model for one recovery decision. Never raises."""
        started = time.perf_counter()
        try:
            async with self._semaphore:
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=settings.ai_max_output_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            # The system prompt is identical on every call in a run, so
                            # caching it turns a large simulation from costly to cheap.
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": build_user_message(context)}],
                    output_config={
                        "effort": "low",
                        "format": decision_json_schema(context.allowed_actions),
                    },
                )
        except Exception as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            log.warning("ai.llm_call_failed", error=type(exc).__name__, latency_ms=elapsed)
            return LLMResponse(None, elapsed, self.model, error=f"{type(exc).__name__}: {exc}")

        elapsed = int((time.perf_counter() - started) * 1000)
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            return LLMResponse(None, elapsed, self.model, error="empty response")
        try:
            return LLMResponse(json.loads(text), elapsed, self.model)
        except json.JSONDecodeError:
            # Hand the raw string to the validator, which reports it as a validation
            # failure and triggers the deterministic fallback.
            return LLMResponse(text, elapsed, self.model)

    async def aclose(self) -> None:
        await self._client.close()
