"""Uniform response envelope: {"data": ..., "meta": {...}}."""

from __future__ import annotations

from typing import Any


def ok(data: Any, **meta: Any) -> dict:
    return {"data": data, "meta": meta}


def paginated(items: list, *, total: int, page: int, page_size: int, **meta: Any) -> dict:
    return {
        "data": items,
        "meta": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, -(-total // page_size)) if page_size else 1,
            **meta,
        },
    }
