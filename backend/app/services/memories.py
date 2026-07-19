import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models.common import ConflictStatus, MemoryStatus, MemoryType, ModelRole, utc_now
from app.db.models.interview import InterviewConfig, InterviewPlan, InterviewSession, PlanQuestion
from app.db.models.memory import MemoryConflict, MemoryItem, MemorySource, MemoryUsage
from app.db.models.profile import UserProfile
from app.memory.conflicts import resolve_conflict
from app.memory.retriever import retrieve_memories
from app.memory.writer import set_memory_pinned, transition_memory
from app.schemas.memory import (
    ForgetSessionResult,
    MemoryConflictPublic,
    MemoryItemPublic,
    MemoryPreviewItem,
    MemoryPreviewPublic,
    MemorySettingsPublic,
    MemorySourcePublic,
    MemoryUpdate,
)


async def get_memory(
    session: AsyncSession, profile_id: uuid.UUID, memory_id: uuid.UUID
) -> MemoryItem:
    memory = await session.scalar(
        select(MemoryItem).where(
            MemoryItem.id == memory_id,
            MemoryItem.profile_id == profile_id,
            MemoryItem.deleted_at.is_(None),
        )
    )
    if not memory:
        raise AppError(code="memory_not_found", message="记忆不存在", status_code=404)
    return memory


async def memory_public(session: AsyncSession, memory: MemoryItem) -> MemoryItemPublic:
    sources = list(
        (
            await session.scalars(
                select(MemorySource)
                .where(
                    MemorySource.memory_id == memory.id,
                    MemorySource.deleted_at.is_(None),
                )
                .order_by(MemorySource.observed_at.desc())
            )
        ).all()
    )
    conflicts = list(
        (
            await session.scalars(
                select(MemoryConflict).where(
                    (MemoryConflict.memory_id == memory.id)
                    | (MemoryConflict.conflicting_memory_id == memory.id),
                    MemoryConflict.status == ConflictStatus.OPEN,
                    MemoryConflict.deleted_at.is_(None),
                )
            )
        ).all()
    )
    return MemoryItemPublic(
        **memory.model_dump(exclude={"memory_type", "status"}),
        memory_type=MemoryType(memory.memory_type),
        status=MemoryStatus(memory.status),
        sources=[MemorySourcePublic.model_validate(item) for item in sources],
        open_conflicts=[MemoryConflictPublic.model_validate(item) for item in conflicts],
    )


async def list_memories(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    status: MemoryStatus | None = None,
    memory_type: MemoryType | None = None,
) -> list[MemoryItemPublic]:
    statement = select(MemoryItem).where(
        MemoryItem.profile_id == profile_id,
        MemoryItem.deleted_at.is_(None),
    )
    if status:
        statement = statement.where(MemoryItem.status == status)
    if memory_type:
        statement = statement.where(MemoryItem.memory_type == memory_type)
    items = list(
        (
            await session.scalars(
                statement.order_by(
                    MemoryItem.pinned.desc(),
                    MemoryItem.updated_at.desc(),
                    MemoryItem.canonical_key,
                )
            )
        ).all()
    )
    return [await memory_public(session, item) for item in items]


async def update_memory(
    session: AsyncSession,
    memory: MemoryItem,
    payload: MemoryUpdate,
) -> MemoryItem:
    if memory.status == MemoryStatus.CONFLICTED:
        raise AppError(
            code="memory_conflict_unresolved",
            message="请先解决冲突，再编辑这条记忆",
            status_code=409,
        )
    if payload.content is not None:
        memory.content = payload.content.strip()
    if payload.structured_value is not None:
        memory.structured_value = {
            **payload.structured_value,
            "explicit_user_statement": True,
            "origin": "user_edit",
        }
    existing_manual = await session.scalar(
        select(MemorySource.id).where(
            MemorySource.memory_id == memory.id,
            MemorySource.source_type == "user_manual",
            MemorySource.deleted_at.is_(None),
        )
    )
    if not existing_manual:
        session.add(MemorySource(memory_id=memory.id, source_type="user_manual"))
    memory.status = MemoryStatus.ACTIVE
    memory.last_verified_at = utc_now()
    memory.expires_at = None
    memory.touch()
    await session.commit()
    return memory


async def delete_memory(session: AsyncSession, memory: MemoryItem) -> None:
    await session.delete(memory)
    await session.commit()


async def get_settings(profile: UserProfile) -> MemorySettingsPublic:
    return MemorySettingsPublic(memory_enabled=profile.memory_enabled)


async def update_settings(
    session: AsyncSession, profile: UserProfile, *, memory_enabled: bool
) -> MemorySettingsPublic:
    profile.memory_enabled = memory_enabled
    profile.touch()
    await session.commit()
    return await get_settings(profile)


async def preview_for_plan(
    session: AsyncSession,
    profile: UserProfile,
    plan_id: uuid.UUID,
) -> MemoryPreviewPublic:
    plan = await session.scalar(
        select(InterviewPlan)
        .join(InterviewConfig, InterviewConfig.id == InterviewPlan.config_id)
        .where(
            InterviewPlan.id == plan_id,
            InterviewConfig.profile_id == profile.id,
            InterviewPlan.deleted_at.is_(None),
        )
    )
    if not plan:
        raise AppError(code="interview_plan_not_found", message="面试计划不存在", status_code=404)
    config = await session.get(InterviewConfig, plan.config_id)
    questions = list(
        (
            await session.scalars(
                select(PlanQuestion)
                .where(PlanQuestion.plan_id == plan.id, PlanQuestion.deleted_at.is_(None))
                .order_by(PlanQuestion.sequence)
            )
        ).all()
    )
    query = " ".join(
        [config.role_name if config else "", *[item.prompt_snapshot for item in questions]]
    )
    hits = await retrieve_memories(
        session,
        profile_id=profile.id,
        agent_role=ModelRole.INTERVIEWER,
        query=query,
        company_id=config.company_id if config else None,
        role_name=config.role_name if config else None,
        limit=8,
    )
    return MemoryPreviewPublic(
        enabled=profile.memory_enabled,
        items=[
            MemoryPreviewItem(
                id=hit.memory.id,
                memory_type=hit.memory.memory_type,
                content=hit.memory.content,
                pinned=hit.memory.pinned,
                reason=hit.reason,
            )
            for hit in hits
        ],
    )


async def forget_session(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    session_id: uuid.UUID,
) -> ForgetSessionResult:
    interview = await session.scalar(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.profile_id == profile_id,
            InterviewSession.deleted_at.is_(None),
        )
    )
    if not interview:
        raise AppError(
            code="interview_session_not_found",
            message="面试会话不存在",
            status_code=404,
        )
    affected_ids = list(
        (
            await session.scalars(
                select(MemorySource.memory_id)
                .where(
                    MemorySource.session_id == session_id,
                    MemorySource.deleted_at.is_(None),
                )
                .distinct()
            )
        ).all()
    )
    removed_sources_result = await session.execute(
        delete(MemorySource).where(MemorySource.session_id == session_id)
    )
    await session.execute(delete(MemoryUsage).where(MemoryUsage.session_id == session_id))
    deleted_memories = 0
    retained_memories = 0
    for memory_id in affected_ids:
        memory = await session.get(MemoryItem, memory_id)
        if not memory:
            continue
        remaining = await session.scalar(
            select(func.count(MemorySource.id)).where(
                MemorySource.memory_id == memory.id,
                MemorySource.deleted_at.is_(None),
            )
        )
        if not remaining:
            await session.delete(memory)
            deleted_memories += 1
            continue
        retained_memories += 1
        if memory.memory_type in {MemoryType.STABLE_SKILL, MemoryType.RECURRING_GAP}:
            sessions = await session.scalar(
                select(func.count(func.distinct(MemorySource.session_id))).where(
                    MemorySource.memory_id == memory.id,
                    MemorySource.session_id.is_not(None),
                    MemorySource.deleted_at.is_(None),
                )
            )
            if int(sessions or 0) < 2:
                memory.status = MemoryStatus.PROPOSED
                memory.pinned = False
        memory.confidence = max(0.1, memory.confidence * 0.9)
        memory.touch()
    await session.commit()
    return ForgetSessionResult(
        session_id=session_id,
        removed_sources=max(0, int(removed_sources_result.rowcount or 0)),
        deleted_memories=deleted_memories,
        retained_memories=retained_memories,
    )


async def resolve_memory_conflict(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    conflict_id: uuid.UUID,
    winning_memory_id: uuid.UUID,
) -> MemoryItem:
    await resolve_conflict(
        session,
        profile_id=profile_id,
        conflict_id=conflict_id,
        winning_memory_id=winning_memory_id,
    )
    return await get_memory(session, profile_id, winning_memory_id)


confirm_memory = transition_memory
pin_memory = set_memory_pinned
