import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db.models.common import MemoryStatus, MemoryType, ModelRole
from app.db.models.memory import MemoryConflict, MemoryItem, MemorySource, MemoryUsage
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.memory.retriever import retrieve_memories
from app.memory.types import MemoryCandidate, MemorySourceInput
from app.memory.writer import remember


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


def _candidate(
    memory_type: MemoryType,
    key: str,
    content: str,
) -> MemoryCandidate:
    return MemoryCandidate(
        memory_type=memory_type,
        canonical_key=key,
        content=content,
        confidence=0.9,
        explicit_user_statement=True,
        source=MemorySourceInput(source_type="user_manual"),
    )


@pytest.mark.asyncio
async def test_retrieval_respects_role_status_and_user_switch() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="检索测试")
        session.add(profile)
        await session.commit()
        project = await remember(
            session,
            profile_id=profile.id,
            candidate=_candidate(
                MemoryType.PROJECT_FACT,
                "project.rag.role",
                "我在 RAG 项目负责召回、重排和离线评测。",
            ),
        )
        preference = await remember(
            session,
            profile_id=profile.id,
            candidate=_candidate(
                MemoryType.COMMUNICATION_PREFERENCE,
                "preference.question_style",
                "我偏好面试官一次只提出一个问题。",
            ),
        )
        skill = MemoryItem(
            profile_id=profile.id,
            memory_type=MemoryType.STABLE_SKILL,
            canonical_key="skill.rag",
            content="多次评估显示 RAG 工程能力稳定。",
            status=MemoryStatus.ACTIVE,
            confidence=0.8,
        )
        session.add(skill)
        await session.flush()
        session.add(MemorySource(memory_id=skill.id, source_type="user_manual"))
        await session.commit()

        interviewer_hits = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.INTERVIEWER,
            query="RAG 召回质量如何评估",
        )
        evaluator_hits = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.EVALUATOR,
            query="RAG",
        )
        planner_hits = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.PLANNER,
            query="RAG 工程能力",
        )

        assert [item.memory.id for item in interviewer_hits][:2] == [project.id, preference.id]
        assert skill.id not in {item.memory.id for item in interviewer_hits}
        assert evaluator_hits == []
        assert skill.id in {item.memory.id for item in planner_hits}

        profile.memory_enabled = False
        profile.touch()
        await session.commit()
        disabled_hits = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.INTERVIEWER,
            query="RAG",
        )

    assert disabled_hits == []
