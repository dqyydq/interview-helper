"""Periodic worker hook for temporary question-discovery retention cleanup."""

from app.db.session import async_session_factory
from app.services.discovery_retention import cleanup_expired_discovery_runs


async def run_once() -> int:
    """Remove at most one bounded batch of expired terminal discovery runs."""

    async with async_session_factory() as session:
        return await cleanup_expired_discovery_runs(session)
