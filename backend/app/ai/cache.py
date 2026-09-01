"""Decision cache keyed by context signature.

Two opportunities whose decision-relevant features fall in the same buckets warrant the
same recommendation. Reusing a decision across them is what keeps a 2,000-opportunity
run affordable and fast without pretending every decision was a fresh model call —
cached decisions are recorded as `LLM_CACHED`, so the dashboard never overstates how
much of the run the model actually drove.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.schema import AIDecisionOutput


@dataclass(slots=True)
class DecisionCache:
    entries: dict[str, AIDecisionOutput] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def get(self, signature: str) -> AIDecisionOutput | None:
        hit = self.entries.get(signature)
        if hit is None:
            self.misses += 1
        else:
            self.hits += 1
        return hit

    def put(self, signature: str, decision: AIDecisionOutput) -> None:
        self.entries.setdefault(signature, decision)

    @property
    def size(self) -> int:
        return len(self.entries)

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": self.size}
