"""Deterministic, concurrency-safe randomness.

A single global RNG stream would make simulation outcomes depend on the *order* in
which concurrent AI calls resolve. Instead every draw derives its own stream from
(seed, scope...), so the same seed reproduces the same result regardless of scheduling.
"""

from __future__ import annotations

import hashlib
import random


def derive_seed(seed: int, *scope: object) -> int:
    payload = "|".join([str(seed), *(str(s) for s in scope)]).encode()
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def derive_rng(seed: int, *scope: object) -> random.Random:
    """A fresh Random seeded deterministically by (seed, *scope)."""
    return random.Random(derive_seed(seed, *scope))


def weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    """Pick a key with probability proportional to its weight."""
    if not weights:
        raise ValueError("weighted_choice requires a non-empty mapping")
    keys = list(weights.keys())
    vals = [max(0.0, float(weights[k])) for k in keys]
    total = sum(vals)
    if total <= 0:
        return rng.choice(keys)
    r = rng.random() * total
    upto = 0.0
    for k, v in zip(keys, vals, strict=True):
        upto += v
        if r <= upto:
            return k
    return keys[-1]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
