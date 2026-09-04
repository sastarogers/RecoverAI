import asyncio
from app.db.base import Base
import app.db.models  # ensure models are registered
from app.db.session import engine

async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(init_models())
