from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.api.errors import AppError
from app.db.models.common import (
    EmbeddingProfileStatus,
    JobStatus,
    MemoryStatus,
    MemoryType,
    ModelRole,
    ProviderType,
    utc_now,
)
from app.db.models.company import Company, CompanyStylePack, RoundProfile
from app.db.models.embedding import EmbeddingProfile, MemoryEmbedding, PlanQuestionEmbedding
from app.db.models.interview import InterviewConfig, InterviewPlan, PlanQuestion
from app.db.models.job import BackgroundJob
from app.db.models.memory import MemoryItem, MemorySource
from app.db.models.model_connection import ModelConnection, ModelRoleBinding
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.memory import embedding_index
from app.providers.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderHealth,
    ProviderHealthStatus,
)
from app.workers import embedding_jobs


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.requests: list[EmbeddingRequest] = []
        self.closed = False

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        return EmbeddingResponse(vectors=[[3.0, 4.0, 0.0] for _ in request.texts])

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.HEALTHY, latency_ms=1)

    async def aclose(self) -> None:
        self.closed = True


class MutatingEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self, mutate_after_first_request) -> None:
        super().__init__()
        self._mutate_after_first_request = mutate_after_first_request

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        response = await super().embed(request)
        if self._mutate_after_first_request is not None:
            mutate = self._mutate_after_first_request
            self._mutate_after_first_request = None
            await mutate()
        return response


async def _clear_embedding_index_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(BackgroundJob))
        await session.execute(delete(MemoryEmbedding))
        await session.execute(delete(PlanQuestionEmbedding))
        await session.execute(delete(EmbeddingProfile))
        await session.execute(delete(ModelRoleBinding))
        await session.execute(delete(PlanQuestion))
        await session.execute(delete(InterviewPlan))
        await session.execute(delete(InterviewConfig))
        await session.execute(delete(RoundProfile))
        await session.execute(delete(CompanyStylePack))
        await session.execute(delete(Company))
        await session.execute(delete(MemoryItem))
        await session.execute(delete(ModelConnection))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_embedding_index_rows():
    await _clear_embedding_index_rows()
    yield
    await _clear_embedding_index_rows()
    await engine.dispose()


async def _profile_with_cloud_embedding() -> tuple[UserProfile, ModelConnection]:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Embedding worker test")
        session.add(profile)
        await session.flush()
        connection = ModelConnection(
            profile_id=profile.id,
            name="Embedding endpoint",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://embeddings.example.test/v1",
            encrypted_api_key="test-ciphertext",
            model_name="text-embedding-test",
            context_window_tokens=8_192,
        )
        session.add(connection)
        await session.flush()
        session.add(
            ModelRoleBinding(
                profile_id=profile.id,
                role=ModelRole.EMBEDDING,
                connection_id=connection.id,
            )
        )
        await session.commit()
        return profile, connection


@pytest.mark.asyncio
async def test_rebuild_promotes_verified_profile_and_retires_previous_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, connection = await _profile_with_cloud_embedding()
    async with async_session_factory() as session:
        old = EmbeddingProfile(
            profile_id=profile.id,
            model_connection_id=connection.id,
            target_fingerprint="a" * 64,
            model_name="old-embedding-model",
            model_revision="old",
            vector_dimensions=3,
            normalized=True,
            query_instruction="",
            distance_metric="cosine",
            status=EmbeddingProfileStatus.ACTIVE,
        )
        memory = MemoryItem(
            profile_id=profile.id,
            memory_type=MemoryType.PROJECT_FACT,
            canonical_key="project.rag",
            content="负责 RAG 召回、重排和离线评估。",
            status=MemoryStatus.ACTIVE,
        )
        company = Company(
            profile_id=profile.id,
            name="Embedding Test Company",
            slug=f"embedding-test-{profile.id.hex}",
        )
        session.add_all([old, memory, company])
        await session.flush()
        session.add(MemorySource(memory_id=memory.id, source_type="test"))
        style_pack = CompanyStylePack(company_id=company.id, name="Embedding Test Style")
        session.add(style_pack)
        await session.flush()
        round_profile = RoundProfile(
            style_pack_id=style_pack.id,
            round_key="technical-1",
            name="Technical Interview",
        )
        session.add(round_profile)
        await session.flush()
        config = InterviewConfig(
            profile_id=profile.id,
            company_id=company.id,
            round_profile_id=round_profile.id,
            role_name="LLM Application Engineer",
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
        plan_question = PlanQuestion(
            plan_id=plan.id,
            sequence=1,
            prompt_snapshot="请说明如何评估 RAG 系统的召回质量。",
            selection_reason="检验检索评估能力",
        )
        session.add(plan_question)
        await session.commit()
        queued = await embedding_index.enqueue_embedding_rebuild(session, profile.id)

    provider = FakeEmbeddingProvider()
    monkeypatch.setattr(
        embedding_jobs,
        "build_embedding_provider_for_target",
        lambda _target: provider,
    )

    assert await embedding_jobs.run_once("embedding-test-worker") is True

    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, queued.job.id)
        rebuilt = await session.get(EmbeddingProfile, queued.embedding_profile.id)
        retired = await session.get(EmbeddingProfile, old.id)
        vectors = list(
            (
                await session.scalars(
                    select(MemoryEmbedding).where(
                        MemoryEmbedding.embedding_profile_id == queued.embedding_profile.id
                    )
                )
            ).all()
        )
        plan_vectors = list(
            (
                await session.scalars(
                    select(PlanQuestionEmbedding).where(
                        PlanQuestionEmbedding.embedding_profile_id == queued.embedding_profile.id
                    )
                )
            ).all()
        )

    assert job is not None and job.status == JobStatus.COMPLETED
    assert job.result["phase"] == "completed"
    assert "负责" not in str(job.result)
    assert rebuilt is not None and rebuilt.status == EmbeddingProfileStatus.ACTIVE
    assert rebuilt.vector_dimensions == 3
    assert retired is not None and retired.status == EmbeddingProfileStatus.RETIRED
    assert len(vectors) == 1
    assert vectors[0].memory_id == memory.id
    assert vectors[0].embedding == pytest.approx([0.6, 0.8, 0.0])
    assert len(plan_vectors) == 1
    assert plan_vectors[0].plan_question_id == plan_question.id
    assert plan_vectors[0].company_id == company.id
    assert plan_vectors[0].role_name == config.role_name
    assert provider.closed is True


@pytest.mark.asyncio
async def test_rebuild_yields_without_spending_attempt_when_interview_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, _connection = await _profile_with_cloud_embedding()
    async with async_session_factory() as session:
        queued = await embedding_index.enqueue_embedding_rebuild(session, profile.id)

    monkeypatch.setattr(
        embedding_jobs,
        "profile_has_interviewing_session",
        lambda _session, _profile_id: _always_true(),
    )
    provider = FakeEmbeddingProvider()
    monkeypatch.setattr(
        embedding_jobs,
        "build_embedding_provider_for_target",
        lambda _target: provider,
    )

    assert await embedding_jobs.run_once("embedding-test-worker") is True

    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, queued.job.id)
        rebuilding = await session.get(EmbeddingProfile, queued.embedding_profile.id)

    assert job is not None and job.status == JobStatus.QUEUED
    assert job.attempts == 0
    assert job.result["phase"] == "waiting_for_interview"
    assert rebuilding is not None and rebuilding.status == EmbeddingProfileStatus.BUILDING
    assert provider.requests == []


@pytest.mark.asyncio
async def test_target_change_fails_new_build_without_retiring_active_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, connection = await _profile_with_cloud_embedding()
    async with async_session_factory() as session:
        active = EmbeddingProfile(
            profile_id=profile.id,
            model_connection_id=connection.id,
            target_fingerprint="b" * 64,
            model_name="old-embedding-model",
            model_revision="old",
            vector_dimensions=3,
            normalized=True,
            query_instruction="",
            distance_metric="cosine",
            status=EmbeddingProfileStatus.ACTIVE,
        )
        session.add(active)
        await session.commit()
        queued = await embedding_index.enqueue_embedding_rebuild(session, profile.id)
        current = await session.get(ModelConnection, connection.id)
        assert current is not None
        current.model_name = "different-embedding-model"
        current.touch()
        await session.commit()

    provider = FakeEmbeddingProvider()
    monkeypatch.setattr(
        embedding_jobs,
        "build_embedding_provider_for_target",
        lambda _target: provider,
    )

    assert await embedding_jobs.run_once("embedding-test-worker") is True

    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, queued.job.id)
        failed = await session.get(EmbeddingProfile, queued.embedding_profile.id)
        preserved = await session.get(EmbeddingProfile, active.id)

    assert job is not None and job.status == JobStatus.FAILED
    assert job.error_code == "embedding_target_changed"
    assert failed is not None and failed.status == EmbeddingProfileStatus.FAILED
    assert preserved is not None and preserved.status == EmbeddingProfileStatus.ACTIVE
    assert provider.requests == []


@pytest.mark.asyncio
async def test_rebuild_reconciles_a_memory_changed_after_its_first_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, _connection = await _profile_with_cloud_embedding()
    async with async_session_factory() as session:
        memory = MemoryItem(
            profile_id=profile.id,
            memory_type=MemoryType.PROJECT_FACT,
            canonical_key="project.reconciliation",
            content="Initial retrieval evaluation experience.",
            status=MemoryStatus.ACTIVE,
        )
        session.add(memory)
        await session.flush()
        session.add(MemorySource(memory_id=memory.id, source_type="test"))
        await session.commit()
        queued = await embedding_index.enqueue_embedding_rebuild(session, profile.id)

    async def mutate_memory_after_first_request() -> None:
        async with async_session_factory() as session:
            persisted = await session.get(MemoryItem, memory.id)
            assert persisted is not None
            persisted.content = "Updated retrieval evaluation experience with regression coverage."
            persisted.touch()
            await session.commit()

    provider = MutatingEmbeddingProvider(mutate_memory_after_first_request)
    monkeypatch.setattr(
        embedding_jobs,
        "build_embedding_provider_for_target",
        lambda _target: provider,
    )

    assert await embedding_jobs.run_once("embedding-reconciliation-worker") is True

    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, queued.job.id)
        vector = await session.scalar(
            select(MemoryEmbedding).where(
                MemoryEmbedding.embedding_profile_id == queued.embedding_profile.id,
                MemoryEmbedding.memory_id == memory.id,
            )
        )
        persisted_memory = await session.get(MemoryItem, memory.id)

    assert job is not None and job.status == JobStatus.COMPLETED
    assert vector is not None
    assert persisted_memory is not None
    assert vector.source_version == persisted_memory.version
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_worker_recovers_a_lost_embedding_job_without_reclaiming_fresh_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_profile, _stale_connection = await _profile_with_cloud_embedding()
    fresh_profile, _fresh_connection = await _profile_with_cloud_embedding()
    stale_lock = utc_now() - timedelta(seconds=embedding_jobs._embedding_job_lease_seconds() + 1)
    fresh_lock = utc_now()
    async with async_session_factory() as session:
        stale = await embedding_index.enqueue_embedding_rebuild(session, stale_profile.id)
        fresh = await embedding_index.enqueue_embedding_rebuild(session, fresh_profile.id)
        stale_job = await session.get(BackgroundJob, stale.job.id)
        fresh_job = await session.get(BackgroundJob, fresh.job.id)
        assert stale_job is not None
        assert fresh_job is not None
        stale_job.status = JobStatus.RUNNING
        stale_job.attempts = 1
        stale_job.progress = 0.4
        stale_job.locked_at = stale_lock
        stale_job.locked_by = "lost-embedding-worker"
        stale_job.touch(at=stale_lock)
        fresh_job.status = JobStatus.RUNNING
        fresh_job.attempts = 1
        fresh_job.progress = 0.4
        fresh_job.locked_at = fresh_lock
        fresh_job.locked_by = "healthy-embedding-worker"
        fresh_job.touch(at=fresh_lock)
        await session.commit()

    provider = FakeEmbeddingProvider()
    monkeypatch.setattr(
        embedding_jobs,
        "build_embedding_provider_for_target",
        lambda _target: provider,
    )

    assert await embedding_jobs.run_once("replacement-embedding-worker") is True

    async with async_session_factory() as session:
        stale_job = await session.get(BackgroundJob, stale.job.id)
        stale_index = await session.get(EmbeddingProfile, stale.embedding_profile.id)
        fresh_job = await session.get(BackgroundJob, fresh.job.id)

    assert stale_job is not None and stale_job.status == JobStatus.COMPLETED
    assert stale_job.attempts == 2
    assert stale_index is not None and stale_index.status == EmbeddingProfileStatus.ACTIVE
    assert fresh_job is not None and fresh_job.status == JobStatus.RUNNING
    assert fresh_job.locked_at == fresh_lock
    assert fresh_job.locked_by == "healthy-embedding-worker"


@pytest.mark.asyncio
async def test_worker_marks_repeatedly_lost_build_failed_without_retiring_active_index() -> None:
    profile, connection = await _profile_with_cloud_embedding()
    stale_lock = utc_now() - timedelta(seconds=embedding_jobs._embedding_job_lease_seconds() + 1)
    async with async_session_factory() as session:
        active = EmbeddingProfile(
            profile_id=profile.id,
            model_connection_id=connection.id,
            target_fingerprint="d" * 64,
            model_name="old-embedding-model",
            model_revision="old",
            vector_dimensions=3,
            normalized=True,
            query_instruction="",
            distance_metric="cosine",
            status=EmbeddingProfileStatus.ACTIVE,
        )
        session.add(active)
        await session.commit()
        queued = await embedding_index.enqueue_embedding_rebuild(session, profile.id)
        job = await session.get(BackgroundJob, queued.job.id)
        assert job is not None
        job.status = JobStatus.RUNNING
        job.attempts = job.max_attempts
        job.progress = 0.7
        job.locked_at = stale_lock
        job.locked_by = "lost-embedding-worker"
        job.touch(at=stale_lock)
        await session.commit()

    assert await embedding_jobs.run_once("replacement-embedding-worker") is True

    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, queued.job.id)
        failed = await session.get(EmbeddingProfile, queued.embedding_profile.id)
        preserved = await session.get(EmbeddingProfile, active.id)

    assert job is not None and job.status == JobStatus.FAILED
    assert job.error_code == "embedding_worker_lost"
    assert job.locked_at is None
    assert job.locked_by is None
    assert failed is not None and failed.status == EmbeddingProfileStatus.FAILED
    assert preserved is not None and preserved.status == EmbeddingProfileStatus.ACTIVE


async def _always_true() -> bool:
    return True


def test_local_tei_batch_budget_is_conservative_for_chinese_text() -> None:
    document = embedding_index._bounded_document_text("职责", "我" * 10_000)

    assert len(document) <= embedding_index.MAX_DOCUMENT_CHARACTERS
    assert (
        embedding_index.EMBEDDING_BATCH_SIZE * embedding_index.MAX_DOCUMENT_CHARACTERS
        <= embedding_index.MAX_BATCH_CHARACTERS
        <= 3_200
    )


@pytest.mark.asyncio
async def test_rebuild_rejects_anthropic_connection_before_creating_job() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Unsupported embedding test")
        session.add(profile)
        await session.flush()
        connection = ModelConnection(
            profile_id=profile.id,
            name="Anthropic endpoint",
            provider_type=ProviderType.ANTHROPIC_COMPATIBLE,
            base_url="https://anthropic.example.test",
            encrypted_api_key="test-ciphertext",
            model_name="claude-test",
            context_window_tokens=8_192,
        )
        session.add(connection)
        await session.flush()
        session.add(
            ModelRoleBinding(
                profile_id=profile.id,
                role=ModelRole.EMBEDDING,
                connection_id=connection.id,
            )
        )
        await session.commit()

        with pytest.raises(AppError) as error:
            await embedding_index.enqueue_embedding_rebuild(session, profile.id)

        jobs = list((await session.scalars(select(BackgroundJob))).all())

    assert error.value.code == "embedding_provider_unsupported"
    assert error.value.status_code == 409
    assert jobs == []
