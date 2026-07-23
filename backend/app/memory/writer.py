import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import MemoryStatus, MemoryType, utc_now
from app.db.models.memory import MemoryItem, MemorySource
from app.memory.conflicts import create_conflict
from app.memory.types import MemoryCandidate, activation_status

TRANSITIONS: dict[MemoryStatus, set[MemoryStatus]] = {
    MemoryStatus.PROPOSED: {MemoryStatus.ACTIVE, MemoryStatus.REJECTED, MemoryStatus.EXPIRED},
    MemoryStatus.ACTIVE: {
        MemoryStatus.CONFLICTED,
        MemoryStatus.REJECTED,
        MemoryStatus.EXPIRED,
    },
    MemoryStatus.CONFLICTED: {
        MemoryStatus.ACTIVE,
        MemoryStatus.REJECTED,
        MemoryStatus.EXPIRED,
    },
    MemoryStatus.REJECTED: set(),
    MemoryStatus.EXPIRED: set(),
}


def normalize_canonical_key(value: str) -> str:
    normalized = re.sub(r"\s+", "_", value.strip().lower())
    normalized = re.sub(r"[^\w\-.:\u4e00-\u9fff]", "", normalized)
    if not normalized:
        raise AppError(code="memory_key_invalid", message="记忆键不能为空", status_code=422)
    return normalized[:255]


def _same_content(first: str, second: str) -> bool:
    return " ".join(first.split()).casefold() == " ".join(second.split()).casefold()


async def _add_source(
    session: AsyncSession,
    memory: MemoryItem,
    candidate: MemoryCandidate,
) -> None:
    source = candidate.source
    existing = await session.scalar(
        select(MemorySource.id).where(
            MemorySource.memory_id == memory.id,
            MemorySource.session_id == source.session_id,
            MemorySource.message_id == source.message_id,
            MemorySource.source_type == source.source_type,
            MemorySource.deleted_at.is_(None),
        )
    )
    if existing:
        return
    session.add(
        MemorySource(
            memory_id=memory.id,
            session_id=source.session_id,
            message_id=source.message_id,
            source_type=source.source_type,
            evidence_excerpt=(source.evidence_excerpt or "")[:1_000] or None,
        )
    )
    await session.flush()


async def _independent_session_count(session: AsyncSession, memory_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(func.count(func.distinct(MemorySource.session_id))).where(
            MemorySource.memory_id == memory_id,
            MemorySource.session_id.is_not(None),
            MemorySource.deleted_at.is_(None),
        )
    )
    return int(value or 0)


async def remember(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    candidate: MemoryCandidate,
) -> MemoryItem:
    key = normalize_canonical_key(candidate.canonical_key)
    content = candidate.content.strip()
    if not content:
        raise AppError(code="memory_content_empty", message="记忆内容不能为空", status_code=422)
    if not 0 <= candidate.confidence <= 1:
        raise AppError(code="memory_confidence_invalid", message="记忆置信度无效", status_code=422)
    if (
        candidate.source.session_id is None
        and candidate.source.message_id is None
        and candidate.source.source_type != "user_manual"
    ):
        raise AppError(
            code="memory_source_required",
            message="自动记忆必须引用会话或消息来源",
            status_code=422,
        )
    versions = list(
        (
            await session.scalars(
                select(MemoryItem)
                .where(
                    MemoryItem.profile_id == profile_id,
                    MemoryItem.canonical_key == key,
                    MemoryItem.deleted_at.is_(None),
                )
                .order_by(MemoryItem.memory_version.desc())
            )
        ).all()
    )
    same = next(
        (
            item
            for item in versions
            if item.status != MemoryStatus.EXPIRED and _same_content(item.content, content)
        ),
        None,
    )
    if same:
        if same.status == MemoryStatus.REJECTED:
            return same
        await _add_source(session, same, candidate)
        session_count = await _independent_session_count(session, same.id)
        next_status = activation_status(
            MemoryType(same.memory_type),
            explicit_user_statement=candidate.explicit_user_statement,
            independent_session_count=session_count,
        )
        if same.status == MemoryStatus.PROPOSED and next_status == MemoryStatus.ACTIVE:
            same.status = MemoryStatus.ACTIVE
            same.last_verified_at = utc_now()
        same.confidence = max(same.confidence, candidate.confidence)
        same.touch()
        await session.commit()
        await session.refresh(same)
        return same

    memory = MemoryItem(
        profile_id=profile_id,
        memory_type=candidate.memory_type,
        canonical_key=key,
        memory_version=max((item.memory_version for item in versions), default=0) + 1,
        content=content,
        structured_value={
            **candidate.structured_value,
            "explicit_user_statement": candidate.explicit_user_statement,
        },
        status=activation_status(
            candidate.memory_type,
            explicit_user_statement=candidate.explicit_user_statement,
            independent_session_count=1 if candidate.source.session_id else 0,
        ),
        confidence=candidate.confidence,
        last_verified_at=utc_now() if candidate.explicit_user_statement else None,
    )
    session.add(memory)
    await session.flush()
    await _add_source(session, memory, candidate)
    conflicting = next(
        (
            item
            for item in versions
            if item.status
            in {
                MemoryStatus.PROPOSED,
                MemoryStatus.ACTIVE,
                MemoryStatus.CONFLICTED,
            }
        ),
        None,
    )
    if conflicting:
        await create_conflict(session, conflicting, memory)
    await session.commit()
    await session.refresh(memory)
    return memory


async def transition_memory(
    session: AsyncSession,
    memory: MemoryItem,
    target: MemoryStatus,
) -> MemoryItem:
    current = MemoryStatus(memory.status)
    if target == current:
        return memory
    if target not in TRANSITIONS[current]:
        raise AppError(
            code="memory_transition_invalid",
            message=f"记忆不能从 {current.value} 变更为 {target.value}",
            status_code=409,
        )
    if target == MemoryStatus.ACTIVE:
        source_exists = await session.scalar(
            select(MemorySource.id).where(
                MemorySource.memory_id == memory.id,
                MemorySource.deleted_at.is_(None),
            )
        )
        if not source_exists:
            raise AppError(
                code="memory_activation_requires_source",
                message="没有有效来源的记忆不能被激活",
                status_code=409,
            )
    memory.status = target
    if target != MemoryStatus.ACTIVE:
        memory.pinned = False
    if target == MemoryStatus.ACTIVE:
        memory.last_verified_at = utc_now()
    if target == MemoryStatus.EXPIRED:
        memory.expires_at = utc_now()
    memory.touch()
    await session.commit()
    return memory


async def set_memory_pinned(
    session: AsyncSession,
    memory: MemoryItem,
    pinned: bool,
) -> MemoryItem:
    if pinned and memory.status != MemoryStatus.ACTIVE:
        raise AppError(
            code="memory_pin_requires_active",
            message="只有已确认的有效记忆可以固定",
            status_code=409,
        )
    memory.pinned = pinned
    memory.touch()
    await session.commit()
    return memory
