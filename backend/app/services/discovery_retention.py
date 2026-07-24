"""Hard-delete expired temporary discovery data without losing imported provenance."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.common import DiscoveryRunStatus, utc_now
from app.db.models.discovery import QuestionDiscoveryRun

DEFAULT_CLEANUP_BATCH_SIZE = 100
_TERMINAL_STATUSES = (
    DiscoveryRunStatus.SUCCEEDED,
    DiscoveryRunStatus.PARTIAL,
    DiscoveryRunStatus.NO_RESULTS,
    DiscoveryRunStatus.FAILED,
    DiscoveryRunStatus.CANCELLED,
)


async def cleanup_expired_discovery_runs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_CLEANUP_BATCH_SIZE,
) -> int:
    """Delete one bounded batch of terminal discovery runs past their retention date.

    Source, candidate, and evidence rows are temporary and cascade from a run. Import
    audit rows and imported-question provenance use database-level ``SET NULL`` links,
    so their durable attribution snapshots survive the cleanup.
    """

    if limit < 1:
        raise ValueError("limit must be positive")

    cutoff = now or utc_now()
    runs = (
        await session.scalars(
            select(QuestionDiscoveryRun)
            .where(
                QuestionDiscoveryRun.status.in_(_TERMINAL_STATUSES),
                QuestionDiscoveryRun.expires_at <= cutoff,
                QuestionDiscoveryRun.deleted_at.is_(None),
            )
            .order_by(QuestionDiscoveryRun.expires_at, QuestionDiscoveryRun.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for run in runs:
        await session.delete(run)
    await session.commit()
    return len(runs)
