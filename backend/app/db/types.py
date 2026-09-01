"""Dialect-portable column types.

PostgreSQL is the production and demo target (native UUID + JSONB). The same models
also need to run on SQLite so the test suite is fast and requires no Docker daemon,
so every column type here carries a Postgres variant and a portable fallback.
"""

from __future__ import annotations

from sqlalchemy import JSON, Uuid
from sqlalchemy.dialects.postgresql import JSONB

#: JSONB on Postgres, plain JSON elsewhere.
JSONType = JSON().with_variant(JSONB, "postgresql")

#: Native uuid on Postgres, CHAR(32) elsewhere.
UUIDType = Uuid(as_uuid=True)
