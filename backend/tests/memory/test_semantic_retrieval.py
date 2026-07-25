import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.models.common import EmbeddingProfileStatus, MemoryStatus, MemoryType, ModelRole
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.models.embedding import EmbeddingProfile, MemoryEmbedding, PlanQuestionEmbedding
from app.db.models.interview import InterviewConfig, InterviewPlan, PlanQuestion
from app.db.models.memory import MemoryConflict, MemoryItem, MemorySource, MemoryUsage
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.memory import embedding_index, retriever
from app.memory.retriever import retrieve_memories


async def _clear_semantic_retrieval_data() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(MemoryUsage))
        await session.execute(delete(MemoryConflict))
        await session.execute(delete(MemoryEmbedding))
        await session.execute(delete(PlanQuestionEmbedding))
        await session.execute(delete(EmbeddingProfile))
        await session.execute(delete(MemorySource))
        await session.execute(delete(MemoryItem))
        await session.execute(delete(PlanQuestion))
        await session.execute(delete(InterviewPlan))
        await session.execute(delete(InterviewConfig))
        await session.execute(delete(RoundProfile))
        await session.execute(delete(CompanyStylePack))
        await session.execute(delete(Company))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_semantic_retrieval_data():
    await _clear_semantic_retrieval_data()
    yield
    await _clear_semantic_retrieval_data()
    await engine.dispose()


async def _seed_cached_question_space() -> tuple[
    UserProfile,
    MemoryItem,
    EmbeddingProfile,
    PlanQuestion,
]:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Semantic retrieval test")
        session.add(profile)
        await session.flush()
        company = Company(
            profile_id=profile.id,
            name="Semantic Cache Company",
            slug=f"semantic-cache-{uuid.uuid4().hex}",
        )
        session.add(company)
        await session.flush()
        style_pack = CompanyStylePack(company_id=company.id, name="Semantic Cache Style")
        session.add(style_pack)
        await session.flush()
        round_profile = RoundProfile(
            style_pack_id=style_pack.id,
            round_key="semantic-round",
            name="Semantic Round",
        )
        session.add(round_profile)
        await session.flush()
        config = InterviewConfig(
            profile_id=profile.id,
            company_id=company.id,
            round_profile_id=round_profile.id,
            role_name="Applied AI Engineer",
        )
        session.add(config)
        await session.flush()
        plan = InterviewPlan(
            config_id=config.id,
            style_pack_id=style_pack.id,
            total_minutes=45,
        )
        session.add(plan)
        await session.flush()
        question = PlanQuestion(
            plan_id=plan.id,
            sequence=1,
            prompt_snapshot="How would you evaluate retrieval quality for a production RAG system?",
            selection_reason="Tests semantic retrieval experience.",
        )
        memory = MemoryItem(
            profile_id=profile.id,
            memory_type=MemoryType.PROJECT_FACT,
            canonical_key="project.retrieval.evaluation",
            content="Built an offline evaluation loop for vector retrieval quality.",
            status=MemoryStatus.ACTIVE,
            confidence=0.9,
        )
        embedding_profile = EmbeddingProfile(
            profile_id=profile.id,
            local_capability_key="multilingual-e5-small",
            target_fingerprint="a" * 64,
            model_name="interview-helper-local-embedding",
            model_revision="test-revision",
            vector_dimensions=3,
            normalized=True,
            distance_metric="cosine",
            status=EmbeddingProfileStatus.ACTIVE,
        )
        session.add_all([question, memory, embedding_profile])
        await session.flush()
        session.add(MemorySource(memory_id=memory.id, source_type="user_manual"))
        session.add(
            MemoryEmbedding(
                profile_id=profile.id,
                embedding_profile_id=embedding_profile.id,
                memory_id=memory.id,
                content_hash="b" * 64,
                source_version=memory.version,
                embedding=[1.0, 0.0, 0.0],
            )
        )
        session.add(
            PlanQuestionEmbedding(
                profile_id=profile.id,
                embedding_profile_id=embedding_profile.id,
                plan_id=plan.id,
                plan_question_id=question.id,
                company_id=company.id,
                role_name=config.role_name,
                content_hash="c" * 64,
                source_version=question.version,
                embedding=[1.0, 0.0, 0.0],
            )
        )
        await session.commit()
        return profile, memory, embedding_profile, question


@pytest.mark.asyncio
async def test_retrieval_uses_active_cached_question_vector_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, memory, _embedding_profile, question = await _seed_cached_question_space()

    def unexpected_provider(*_: object, **__: object) -> object:
        raise AssertionError("live retrieval must not construct an embedding provider")

    monkeypatch.setattr(
        embedding_index,
        "build_embedding_provider_for_target",
        unexpected_provider,
    )
    async with async_session_factory() as session:
        hits = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.INTERVIEWER,
            query="redis timeout diagnostics",
            semantic_plan_question_id=question.id,
        )

    assert [hit.memory.id for hit in hits] == [memory.id]
    assert "text=0.00" in hits[0].reason
    assert "semantic=1.00" in hits[0].reason


@pytest.mark.asyncio
async def test_retrieval_falls_back_to_fts_when_cached_semantic_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, memory, _embedding_profile, question = await _seed_cached_question_space()
    async with async_session_factory() as session:
        baseline = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.INTERVIEWER,
            query="retrieval quality",
        )

        async def broken_cache(*_: object, **__: object) -> list[object]:
            raise RuntimeError("cached vector query failed")

        monkeypatch.setattr(retriever, "_read_cached_semantic_candidates", broken_cache)
        fallback = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.INTERVIEWER,
            query="retrieval quality",
            semantic_plan_question_id=question.id,
        )

    assert [hit.memory.id for hit in baseline] == [memory.id]
    assert [hit.memory.id for hit in fallback] == [hit.memory.id for hit in baseline]
    assert [hit.reason for hit in fallback] == [hit.reason for hit in baseline]
    assert [hit.score for hit in fallback] == pytest.approx([hit.score for hit in baseline])


@pytest.mark.asyncio
async def test_retrieval_ignores_cached_vectors_from_non_active_profiles() -> None:
    profile, memory, embedding_profile, question = await _seed_cached_question_space()
    async with async_session_factory() as session:
        persisted_profile = await session.get(EmbeddingProfile, embedding_profile.id)
        assert persisted_profile is not None
        persisted_profile.status = EmbeddingProfileStatus.RETIRED
        persisted_profile.touch()
        await session.commit()
        hits = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.INTERVIEWER,
            query="redis timeout diagnostics",
            semantic_plan_question_id=question.id,
        )

    assert memory.id not in {hit.memory.id for hit in hits}


@pytest.mark.asyncio
async def test_retrieval_ignores_stale_cached_source_versions() -> None:
    profile, memory, _embedding_profile, question = await _seed_cached_question_space()
    async with async_session_factory() as session:
        persisted_memory = await session.get(MemoryItem, memory.id)
        assert persisted_memory is not None
        persisted_memory.touch()
        await session.commit()
        stale_memory_hits = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.INTERVIEWER,
            query="redis timeout diagnostics",
            semantic_plan_question_id=question.id,
        )

        refreshed_memory_embedding = await session.scalar(
            select(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory.id)
        )
        assert refreshed_memory_embedding is not None
        refreshed_memory_embedding.source_version = persisted_memory.version
        persisted_question = await session.get(PlanQuestion, question.id)
        assert persisted_question is not None
        persisted_question.touch()
        await session.commit()
        stale_question_hits = await retrieve_memories(
            session,
            profile_id=profile.id,
            agent_role=ModelRole.INTERVIEWER,
            query="redis timeout diagnostics",
            semantic_plan_question_id=question.id,
        )

    assert stale_memory_hits == []
    assert stale_question_hits == []
