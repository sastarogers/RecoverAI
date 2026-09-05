"""LLM client wrapper: Google Gemini & Anthropic Claude support.

Kept deliberately thin. It returns raw payloads and never decides anything — parsing
and validation happen in `app.ai.validation`, so a malformed or hostile response is
handled by the same gate regardless of where it came from (§49).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import httpx

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
    """Async client for recovery decisions (Google Gemini or Anthropic Claude)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_concurrency: int | None = None,
        provider: str | None = None,
    ) -> None:
        self.model = model or settings.ai_model
        self.timeout = timeout or settings.ai_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency or settings.ai_max_concurrency)

        # Detect provider
        if provider:
            self.provider = provider
        elif (
            (api_key and "AQ." in api_key)
            or settings.gemini_api_key
            or "gemini" in self.model.lower()
        ):
            self.provider = "gemini"
        elif settings.anthropic_api_key:
            self.provider = "anthropic"
        else:
            self.provider = "gemini"

        if self.provider == "gemini":
            self.api_key = api_key or settings.gemini_api_key
            if not self.api_key:
                raise LLMUnavailable("GEMINI_API_KEY is not configured")
            self._http_client: httpx.AsyncClient | None = httpx.AsyncClient(timeout=self.timeout)
            self._anthropic_client = None
        else:
            self.api_key = api_key or settings.anthropic_api_key
            if not self.api_key:
                raise LLMUnavailable("ANTHROPIC_API_KEY is not configured")
            from anthropic import AsyncAnthropic

            self._anthropic_client = AsyncAnthropic(
                api_key=self.api_key, timeout=self.timeout, max_retries=1
            )
            self._http_client = None

    async def decide(self, context: RecoveryContext) -> LLMResponse:
        """Ask the model for one recovery decision. Never raises."""
        if self.provider == "gemini":
            return await self._decide_gemini(context)
        return await self._decide_anthropic(context)

    async def _decide_gemini(self, context: RecoveryContext) -> LLMResponse:
        started = time.perf_counter()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )

        schema = {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": [str(a) for a in context.allowed_actions],
                },
                "recovery_probability": {
                    "type": "NUMBER",
                    "description": "Estimated chance this action recovers the revenue (0.0 to 1.0)",
                },
                "confidence": {
                    "type": "NUMBER",
                    "description": "Confidence in recommendation (0.0 to 1.0)",
                },
                "reason": {
                    "type": "STRING",
                    "description": "One or two sentences citing the specific signals that drove the choice",
                },
                "risk_level": {
                    "type": "STRING",
                    "enum": ["LOW", "MEDIUM", "HIGH"],
                },
            },
            "required": ["action", "recovery_probability", "confidence", "reason", "risk_level"],
        }

        body = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": build_user_message(context)}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": schema,
                "maxOutputTokens": settings.ai_max_output_tokens,
            },
        }

        used_model = self.model
        try:
            assert self._http_client is not None
            async with self._semaphore:
                resp = await self._http_client.post(url, json=body)
                if resp.status_code in (503, 429) and self.model != "gemini-3.5-flash-lite":
                    log.warning(
                        "ai.gemini_congested_trying_flash_lite",
                        primary_model=self.model,
                        status=resp.status_code,
                    )
                    fallback_url = (
                        f"https://generativelanguage.googleapis.com/v1beta/models/"
                        f"gemini-3.5-flash-lite:generateContent?key={self.api_key}"
                    )
                    resp = await self._http_client.post(fallback_url, json=body)
                    used_model = "gemini-3.5-flash-lite"
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            log.warning("ai.gemini_call_failed", error=type(exc).__name__, latency_ms=elapsed)
            return LLMResponse(None, elapsed, used_model, error=f"{type(exc).__name__}: {exc}")

        elapsed = int((time.perf_counter() - started) * 1000)
        candidates = data.get("candidates", [])
        if not candidates:
            return LLMResponse(None, elapsed, used_model, error="No candidates returned by Gemini")

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        if not text.strip():
            return LLMResponse(None, elapsed, used_model, error="empty response from Gemini")

        try:
            return LLMResponse(json.loads(text), elapsed, used_model)
        except json.JSONDecodeError:
            return LLMResponse(text, elapsed, used_model)

    async def _decide_anthropic(self, context: RecoveryContext) -> LLMResponse:
        started = time.perf_counter()
        try:
            assert self._anthropic_client is not None
            async with self._semaphore:
                response = await self._anthropic_client.messages.create(
                    model=self.model,
                    max_tokens=settings.ai_max_output_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
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
            return LLMResponse(text, elapsed, self.model)

    async def aclose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
        if self._anthropic_client is not None:
            await self._anthropic_client.close()
