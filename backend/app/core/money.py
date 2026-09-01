"""Money helpers.

Every monetary value in RecoverAI is stored and transported as an integer number of
**minor units** (paise for INR). Floats never touch stored amounts.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MINOR_PER_MAJOR = 100


def to_minor(major: float | int | str | Decimal) -> int:
    """Rupees -> paise, half-up rounded."""
    return int(
        (Decimal(str(major)) * MINOR_PER_MAJOR).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def to_major(minor: int) -> float:
    """Paise -> rupees, for display/AI context only (never for storage)."""
    return float(Decimal(minor) / MINOR_PER_MAJOR)


def format_inr(minor: int) -> str:
    """Indian digit grouping: 500000 -> '₹5,000'."""
    negative = minor < 0
    value = Decimal(abs(minor)) / MINOR_PER_MAJOR
    whole = int(value)
    frac = int(round((value - whole) * 100))
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    out = "₹" + s + (f".{frac:02d}" if frac else "")
    return f"-{out}" if negative else out


def format_compact_inr(minor: int) -> str:
    """Dashboard headline formatting: 1240000000 -> '₹12.4L'."""
    rupees = Decimal(abs(minor)) / MINOR_PER_MAJOR
    sign = "-" if minor < 0 else ""
    if rupees >= 10_000_000:
        return f"{sign}₹{rupees / 10_000_000:.2f}Cr"
    if rupees >= 100_000:
        return f"{sign}₹{rupees / 100_000:.1f}L"
    if rupees >= 1_000:
        return f"{sign}₹{rupees / 1_000:.1f}K"
    return f"{sign}₹{rupees:.0f}"
