import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.api.errors import AppError
from app.db.models.common import MemoryStatus, MemoryType
from app.db.models.memory import MemoryConflict, MemoryItem, MemorySource, MemoryUsage
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.memory.conflicts import resolve_conflict
from app.memory.types import MemoryCandidate, MemorySourceInput, activation_status
from app.memory.writer import remember, set_memory_pinned, transition_memory


async def _clear_memory_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(MemoryUsage))
        await session.execute(delete(MemoryConflict))
        await session.execute(delete(MemorySource))
        await session.execute(delete(MemoryItem))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_memory_data():
    await _clear_memory_data()
    yield
    await _clear_memory_data()
    await engine.dispose()


def _manual_candidate(content: str) -> MemoryCandidate:
    return MemoryCandidate(
        memory_type=MemoryType.PROJECT_FACT,
        canonical_key="project.rag.responsibility",
        content=content,
        confidence=0.9,
        explicit_user_statement=True,
        source=MemorySourceInput(source_type="user_manual"),
    )


def test_capability_memory_requires_two_independent_sessions() -> None:
    assert (
        activation_status(
            MemoryType.RECURRING_GAP,
            explicit_user_statement=False,
            independent_session_count=1,
        )
        == MemoryStatus.PROPOSED
    )
    assert (
        activation_status(
            MemoryType.RECURRING_GAP,
            explicit_user_statement=False,
            independent_session_count=2,
        )
        == MemoryStatus.ACTIVE
    )


@pytest.mark.asyncio
async def test_same_fact_deduplicates_and_conflicting_fact_creates_version() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="记忆测试")
        session.add(profile)
        await session.commit()

        first = await remember(
            session,
            profile_id=profile.id,
            candidate=_manual_candidate("我负责 RAG 召回与重排。"),
        )
        repeated = await remember(
            session,
            profile_id=profile.id,
            candidate=_manual_candidate("我负责 RAG 召回与重排。"),
        )
        conflicting = await remember(
            session,
            profile_id=profile.id,
            candidate=_manual_candidate("我只负责 RAG 的评测平台。"),
        )
        source_count = await session.scalar(
            select(func.count(MemorySource.id)).where(MemorySource.memory_id == first.id)
        )
        conflict = await session.scalar(select(MemoryConflict))
        assert first.status == MemoryStatus.CONFLICTED
        assert conflicting.status == MemoryStatus.CONFLICTED
        assert conflict is not None

        await resolve_conflict(
            session,
            profile_id=profile.id,
            conflict_id=conflict.id,
            winning_memory_id=conflicting.id,
        )
        await session.refresh(first)
        await session.refresh(conflicting)

    assert first.id == repeated.id
    assert source_count == 1
    assert conflicting.memory_version == 2
    assert first.status == MemoryStatus.EXPIRED
    assert conflicting.status == MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_rejected_memory_is_not_silently_reactivated_or_pinned() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="记忆测试")
        session.add(profile)
        await session.commit()
        memory = await remember(
            session,
            profile_id=profile.id,
            candidate=_manual_candidate("我负责 RAG 召回与重排。"),
        )
        await transition_memory(session, memory, MemoryStatus.REJECTED)
        repeated = await remember(
            session,
            profile_id=profile.id,
            candidate=_manual_candidate("我负责 RAG 召回与重排。"),
        )

        assert repeated.status == MemoryStatus.REJECTED
        with pytest.raises(AppError) as error:
            await set_memory_pinned(session, repeated, True)

    assert error.value.code == "memory_pin_requires_active"


@pytest.mark.asyncio
async def test_memory_without_source_cannot_be_activated() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="记忆测试")
        session.add(profile)
        await session.flush()
        orphan = MemoryItem(
            profile_id=profile.id,
            memory_type=MemoryType.PRACTICE_GOAL,
            canonical_key="goal.orphan",
            content="练习系统设计",
            status=MemoryStatus.PROPOSED,
        )
        session.add(orphan)
        await session.commit()

        with pytest.raises(AppError) as error:
            await transition_memory(session, orphan, MemoryStatus.ACTIVE)

    assert error.value.code == "memory_activation_requires_source"
