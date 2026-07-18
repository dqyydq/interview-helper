from collections.abc import AsyncIterator

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = structlog.get_logger(__name__)

engine = create_async_engine(
    settings.database_url,
    connect_args={
        "timeout": settings.database_connect_timeout_seconds,
        "command_timeout": 5.0,
    },
    echo=settings.database_echo,
    pool_pre_ping=True,
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


async def database_healthcheck() -> str:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # Health checks must degrade instead of crashing the process.
        await logger.awarning("database_healthcheck_failed", error_type=type(exc).__name__)
        return "unavailable"
    return "connected"


async def dispose_engine() -> None:
    await engine.dispose()
