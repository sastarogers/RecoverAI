"""The recovery agent (§20).

Three modes, one output type:
  * ``heuristic`` — deterministic engine only. Makes a whole run reproducible.
  * ``llm``       — always ask the model; fall back only on failure.
  * ``auto``      — model-driven within a per-run call budget, with a decision cache
                    for repeated context shapes, and the heuristic engine beneath it.

Every decision records *how* it was produced (`decision_source`), so the dashboard can
state plainly what share of the run the model actually drove.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai import heuristic
from app.ai.cache import DecisionCache
from app.ai.llm_client import LLMUnavailable, RecoveryLLMClient
from app.ai.schema import AIDecisionOutput
from app.ai.validation import validate_ai_output
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.context import RecoveryContext
from app.domain.enums import DecisionSource

log = get_logger("recoverai.ai.agent")


@dataclass(slots=True)
class AgentDecision:
    """A validated decision plus the provenance needed for the audit trail."""

    output: AIDecisionOutput
    source: DecisionSource
    model: str | None = None
    latency_ms: int | None = None
    context_signature: str | None = None
    raw_response: dict | None = None
    validation_errors: list[str] | None = None


class RecoveryAgent:
    """Produces a validated recovery recommendation for an opportunity."""

    def __init__(
        self,
        *,
        mode: str | None = None,
        llm_budget: int | None = None,
        client: RecoveryLLMClient | None = None,
        cache: DecisionCache | None = None,
    ) -> None:
        self.mode = mode or settings.ai_mode
        self.cache = cache if cache is not None else DecisionCache()
        self.llm_budget = (
            llm_budget if llm_budget is not None else settings.ai_llm_budget_per_run
        )
        self.llm_calls = 0
        self.fallback_count = 0
        self._client = client
        self._client_failed = False

        if self.mode != "heuristic" and self._client is None:
            try:
                self._client = RecoveryLLMClient()
            except LLMUnavailable:
                # No key configured: degrade to the deterministic engine rather than
                # failing the run. The mode is reported honestly in the run record.
                log.info("ai.llm_unavailable_falling_back_to_heuristic")
                self.mode = "heuristic"

    # -- public API ------------------------------------------------------

    @property
    def effective_mode(self) -> str:
        return self.mode

    async def decide(self, context: RecoveryContext) -> AgentDecision:
        signature = context.signature()

        if self.mode == "heuristic":
            return AgentDecision(
                output=heuristic.decide(context),
                source=DecisionSource.HEURISTIC,
                context_signature=signature,
            )

        cached = self.cache.get(signature)
        if cached is not None:
            return AgentDecision(
                output=cached,
                source=DecisionSource.LLM_CACHED,
                model=getattr(self._client, "model", None),
                context_signature=signature,
            )

        if self.mode == "auto" and self._budget_exhausted():
            return self._fallback(context, signature, ["llm budget exhausted for this run"])

        if self._client is None or self._client_failed:
            return self._fallback(context, signature, ["llm client unavailable"])

        self.llm_calls += 1
        response = await self._client.decide(context)
        if not response.ok:
            return self._fallback(context, signature, [response.error or "llm call failed"],
                                  latency_ms=response.latency_ms)

        result = validate_ai_output(response.payload, context)
        if not result.ok:
            log.warning(
                "ai.output_rejected",
                opportunity_ref=context.opportunity_ref,
                errors=result.errors,
            )
            return self._fallback(
                context, signature, result.errors, latency_ms=response.latency_ms,
                raw=response.payload if isinstance(response.payload, dict) else None,
            )

        decision = result.decision
        self.cache.put(signature, decision)
        return AgentDecision(
            output=decision,
            source=DecisionSource.LLM,
            model=response.model,
            latency_ms=response.latency_ms,
            context_signature=signature,
            raw_response=response.payload if isinstance(response.payload, dict) else None,
        )

    def stats(self) -> dict:
        return {
            "mode": self.mode,
            "llm_calls": self.llm_calls,
            "llm_budget": self.llm_budget,
            "fallbacks": self.fallback_count,
            "cache": self.cache.stats(),
        }

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    # -- internals -------------------------------------------------------

    def _budget_exhausted(self) -> bool:
        return self.llm_budget >= 0 and self.llm_calls >= self.llm_budget

    def _fallback(
        self,
        context: RecoveryContext,
        signature: str,
        errors: list[str],
        *,
        latency_ms: int | None = None,
        raw: dict | None = None,
    ) -> AgentDecision:
        """Deterministic fallback (§48). The platform never stalls because the AI did."""
        self.fallback_count += 1
        return AgentDecision(
            output=heuristic.decide(context),
            source=DecisionSource.HEURISTIC_FALLBACK,
            model=getattr(self._client, "model", None),
            latency_ms=latency_ms,
            context_signature=signature,
            raw_response=raw,
            validation_errors=errors,
        )
