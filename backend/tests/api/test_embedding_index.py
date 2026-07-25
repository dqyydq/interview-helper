import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import embedding_index as embedding_index_route
from app.db.models.common import EmbeddingProfileStatus, JobStatus, JobType
from app.db.models.embedding import EmbeddingProfile
from app.db.models.job import BackgroundJob
from app.db.models.model_connection import ModelRoleBinding
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.main import app


async def _delete_embedding_api_profile(profile_id: uuid.UUID) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(BackgroundJob).where(BackgroundJob.profile_id == profile_id))
        await session.execute(
            delete(EmbeddingProfile).where(EmbeddingProfile.profile_id == profile_id)
        )
        await session.execute(
            delete(ModelRoleBinding).where(ModelRoleBinding.profile_id == profile_id)
        )
        await session.execute(delete(UserProfile).where(UserProfile.id == profile_id))
        await session.commit()


@pytest_asyncio.fixture
async def embedding_api_profile(monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    async with async_session_factory() as session:
        profile = UserProfile(display_name=f"Embedding API {uuid.uuid4().hex[:10]}")
        session.add(profile)
        await session.commit()
        profile_id = profile.id

    async def ensure_test_profile(session: AsyncSession) -> UserProfile:
        persisted = await session.get(UserProfile, profile_id)
        assert persisted is not None
        return persisted

    monkeypatch.setattr(
        embedding_index_route.model_connections,
        "ensure_local_profile",
        ensure_test_profile,
    )
    yield profile_id
    await _delete_embedding_api_profile(profile_id)
    await engine.dispose()


@pytest.mark.asyncio
async def test_embedding_index_api_reports_safe_status_and_only_queues_rebuild(
    embedding_api_profile: uuid.UUID,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        initial = await client.get("/api/embedding-index")
        unbound = await client.post("/api/embedding-index/rebuild")
        binding = await client.put(
            "/api/model-connections/roles/embedding",
            json={"local_capability_key": "multilingual-e5-small"},
        )
        queued = await client.post("/api/embedding-index/rebuild")
        status = await client.get("/api/embedding-index")

    assert initial.status_code == 200
    assert initial.json() == {
        "active_profile": None,
        "building_profile": None,
        "latest_failed_profile": None,
        "job": None,
        "interview_active": False,
    }
    assert unbound.status_code == 409
    assert unbound.json()["code"] == "model_role_unbound"
    assert binding.status_code == 200
    assert queued.status_code == 202
    assert queued.json()["created"] is True
    assert queued.json()["embedding_profile"]["status"] == "building"
    assert queued.json()["embedding_profile"]["target_kind"] == "local_capability"
    assert queued.json()["job"]["status"] == "queued"
    assert status.status_code == 200
    assert status.json()["building_profile"]["id"] == queued.json()["embedding_profile"]["id"]
    assert status.json()["job"]["id"] == queued.json()["job"]["id"]

    public_response = queued.text + status.text
    for forbidden in (
        "target_fingerprint",
        "local_capability_key",
        "model_connection_id",
        "payload",
        "result",
        "embedding_source_invalid",
    ):
        assert forbidden not in public_response


@pytest.mark.asyncio
async def test_embedding_index_status_surfaces_a_newer_failed_rebuild_while_old_index_stays_active(
    embedding_api_profile: uuid.UUID,
) -> None:
    started_at = datetime(2026, 7, 25, tzinfo=UTC)
    async with async_session_factory() as session:
        active = EmbeddingProfile(
            profile_id=embedding_api_profile,
            local_capability_key="multilingual-e5-small",
            target_fingerprint="a" * 64,
            model_name="interview-helper-local-embedding",
            model_revision="e5-revision",
            vector_dimensions=384,
            status=EmbeddingProfileStatus.ACTIVE,
            created_at=started_at,
            updated_at=started_at,
        )
        failed = EmbeddingProfile(
            profile_id=embedding_api_profile,
            local_capability_key="multilingual-e5-small",
            target_fingerprint="b" * 64,
            model_name="interview-helper-local-embedding",
            model_revision="e5-revision",
            vector_dimensions=384,
            status=EmbeddingProfileStatus.FAILED,
            failure_code="embedding_dimension_mismatch",
            failure_summary="向量索引未能完成；请检查嵌入服务和模型配置后重试。",
            created_at=started_at + timedelta(seconds=1),
            updated_at=started_at + timedelta(seconds=1),
        )
        session.add_all([active, failed])
        await session.flush()
        failed_job = BackgroundJob(
            profile_id=embedding_api_profile,
            job_type=JobType.EMBEDDING_REINDEX,
            status=JobStatus.FAILED,
            progress=1.0,
            payload={"embedding_profile_id": str(failed.id), "target_fingerprint": "b" * 64},
            result={"phase": "failed"},
            error_code="embedding_dimension_mismatch",
            error_message="safe failure",
            idempotency_key=f"embedding-index-failed-{uuid.uuid4().hex}",
            available_at=started_at + timedelta(seconds=1),
            created_at=started_at + timedelta(seconds=1),
            updated_at=started_at + timedelta(seconds=1),
        )
        session.add(failed_job)
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/embedding-index")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_profile"]["id"] == str(active.id)
    assert payload["latest_failed_profile"]["id"] == str(failed.id)
    assert payload["job"]["id"] == str(failed_job.id)
    assert payload["job"]["status"] == "failed"
    assert payload["job"]["error_code"] == "embedding_dimension_mismatch"
