"""Drop and recreate every table directly from the models.

DESTRUCTIVE. This wipes the target database and rebuilds the schema without going
through Alembic, so the result carries no migration history. It exists for fast local
iteration against a scratch database; `alembic upgrade head` is the supported path for
anything you care about.

Because it reads DATABASE_URL, running it with a normal .env would drop the working
Postgres database. It therefore refuses to touch a non-local host unless you pass
--yes-i-am-sure.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import app.db.models  # noqa: F401  — imported for its side effect: registers every table
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _is_local(url: str) -> bool:
    return url.startswith("sqlite") or any(f"@{h}" in url or f"@{h}:" in url for h in LOCAL_HOSTS)


async def init_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes-i-am-sure",
        action="store_true",
        help="required to run against a non-local database",
    )
    args = parser.parse_args()

    url = settings.database_url
    safe_url = url.split("@")[-1] if "@" in url else url
    if not _is_local(url) and not args.yes_i_am_sure:
        print(f"refusing to drop a non-local database ({safe_url}).", file=sys.stderr)
        print("re-run with --yes-i-am-sure if that is really what you want.", file=sys.stderr)
        return 2

    print(f"dropping and recreating all tables on {safe_url} ...")
    asyncio.run(init_models())
    print("done. note: no Alembic history — use `alembic upgrade head` for real schemas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
