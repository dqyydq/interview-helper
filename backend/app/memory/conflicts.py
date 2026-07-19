import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import ConflictStatus, MemoryStatus, utc_now
from app.db.models.memory import MemoryConflict, MemoryItem


async def create_conflict(
    session: AsyncSession,
    first: MemoryItem,
    second: MemoryItem,
) -> MemoryConflict:
    memory_id, conflicting_id = sorted((first.id, second.id), key=str)
    existing = await session.scalar(
        select(MemoryConflict).where(
            MemoryConflict.memory_id == memory_id,
            MemoryConflict.conflicting_memory_id == conflicting_id,
            MemoryConflict.deleted_at.is_(None),
        )
    )
    if existing:
        return existing
    first.status = MemoryStatus.CONFLICTED
    second.status = MemoryStatus.CONFLICTED
    first.pinned = False
    second.pinned = False
    first.touch()
    second.touch()
    conflict = MemoryConflict(memory_id=memory_id, conflicting_memory_id=conflicting_id)
    session.add(conflict)
    await session.flush()
    return conflict


async def resolve_conflict(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    conflict_id: uuid.UUID,
    winning_memory_id: uuid.UUID,
) -> MemoryConflict:
    conflict = await session.scalar(
        select(MemoryConflict)
        .join(
            MemoryItem,
            or_(
                MemoryItem.id == MemoryConflict.memory_id,
                MemoryItem.id == MemoryConflict.conflicting_memory_id,
            ),
        )
        .where(
            MemoryConflict.id == conflict_id,
            MemoryItem.profile_id == profile_id,
            MemoryConflict.deleted_at.is_(None),
        )
    )
    if not conflict:
        raise AppError(code="memory_conflict_not_found", message="记忆冲突不存在", status_code=404)
    candidate_ids = {conflict.memory_id, conflict.conflicting_memory_id}
    if winning_memory_id not in candidate_ids:
        raise AppError(
            code="memory_conflict_winner_invalid",
            message="胜出记忆不属于当前冲突",
            status_code=422,
        )
    winner = await session.get(MemoryItem, winning_memory_id)
    loser = await session.get(MemoryItem, (candidate_ids - {winning_memory_id}).pop())
    if (
        not winner
        or not loser
        or winner.profile_id != profile_id
        or loser.profile_id != profile_id
    ):
        raise AppError(code="memory_not_found", message="冲突记忆不存在", status_code=404)
    now = utc_now()
    winner.status = MemoryStatus.ACTIVE
    winner.last_verified_at = now
    winner.touch(at=now)
    loser.status = MemoryStatus.EXPIRED
    loser.pinned = False
    loser.expires_at = now
    loser.touch(at=now)
    conflict.status = ConflictStatus.RESOLVED
    conflict.resolution = f"selected:{winner.id}"
    conflict.resolved_at = now
    conflict.touch(at=now)
    await session.commit()
    return conflict
