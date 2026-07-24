import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.api.errors import AppError
from app.db.models.common import (
    Difficulty,
    DiscoveryRunStatus,
    DiscoverySourceMode,
    DiscoverySourceStatus,
    JobType,
    QuestionType,
)
from app.db.models.discovery import (
    DiscoveryConnector,
    QuestionDiscoveryCandidate,
    QuestionDiscoveryCandidateEvidence,
    QuestionDiscoveryRun,
    QuestionDiscoverySource,
)
from app.db.models.job import BackgroundJob
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.main import app
from app.schemas.discovery import QuestionDiscoveryCreate
from app.services import question_discovery


async def clear_question_discoveries() -> None:
    async with async_session_factory() as session:
        await session.execute(
            delete(BackgroundJob).where(BackgroundJob.job_type == JobType.QUESTION_DISCOVERY)
        )
        await session.execute(delete(QuestionDiscoveryRun))
        await session.execute(delete(DiscoveryConnector))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_question_discoveries():
    await clear_question_discoveries()
    yield
    await clear_question_discoveries()
    await engine.dispose()


def connector_payload(
    *,
    name: str = "Discovery test connector",
    provider_type: str = "tavily",
) -> dict:
    return {
        "name": name,
        "provider_type": provider_type,
        "api_key": "discovery-test-secret",
        "enabled": True,
        "configuration": {},
    }


async def create_connector(
    client: AsyncClient,
    *,
    name: str = "Discovery test connector",
    provider_type: str = "tavily",
) -> str:
    response = await client.post(
        "/api/discovery-connectors",
        json=connector_payload(name=name, provider_type=provider_type),
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_create_search_run_queues_a_profile_scoped_minimal_job() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        connector_id = await create_connector(client)
        created = await client.post(
            "/api/question-discoveries",
            json={
                "connector_id": connector_id,
                "source_mode": "search",
                "company": "字节跳动",
                "round": "二面",
                "role": "LLM 应用开发",
                "skills": ["RAG", "Agent"],
            },
        )
        listed = await client.get("/api/question-discoveries")

    assert created.status_code == 202
    public = created.json()
    assert public["status"] == "queued"
    assert public["stage"] == "queued"
    assert public["query_snapshot"]["source_mode"] == "search"
    assert public["query_snapshot"]["search_query"]
    assert public["query_snapshot"]["domain_policy"]["preset"] == "cn_interview_tech"
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    async with async_session_factory() as session:
        job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == JobType.QUESTION_DISCOVERY)
        )
    assert job is not None
    assert job.payload == {"run_id": public["id"]}
    assert job.idempotency_key.endswith(public["id"])
    assert "discovery-test-secret" not in str(job.payload)


@pytest.mark.asyncio
async def test_run_persists_the_explicitly_selected_firecrawl_connector() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        tavily_connector_id = await create_connector(client, name="Tavily connector")
        firecrawl_connector_id = await create_connector(
            client,
            name="Firecrawl connector",
            provider_type="firecrawl",
        )
        created = await client.post(
            "/api/question-discoveries",
            json={
                "connector_id": firecrawl_connector_id,
                "source_mode": "search",
                "query": "LLM application interview questions",
            },
        )

    assert created.status_code == 202
    public = created.json()
    assert public["connector_id"] == firecrawl_connector_id
    assert public["connector_id"] != tavily_connector_id
    assert public["query_snapshot"]["provider"] == "firecrawl"


@pytest.mark.asyncio
async def test_concurrent_creates_enforce_the_profile_active_run_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every concurrent request must count after its profile lock is acquired."""

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        connector_id = uuid.UUID(await create_connector(client))

    async with async_session_factory() as session:
        connector = await session.get(DiscoveryConnector, connector_id)
        assert connector is not None
        profile_id = connector.profile_id

    payload = QuestionDiscoveryCreate(
        connector_id=connector_id,
        source_mode=DiscoverySourceMode.SEARCH,
        query="LLM retrieval interview questions",
    )
    attempt_count = question_discovery.settings.discovery_max_concurrent_runs_per_profile + 1
    original_lock = question_discovery._lock_profile_active_runs
    all_attempts_ready = asyncio.Event()
    arrived = 0

    async def synchronized_lock(session, locked_profile_id):
        nonlocal arrived
        assert locked_profile_id == profile_id
        arrived += 1
        if arrived == attempt_count:
            all_attempts_ready.set()
        await all_attempts_ready.wait()
        await original_lock(session, locked_profile_id)

    monkeypatch.setattr(question_discovery, "_lock_profile_active_runs", synchronized_lock)

    async def create_once() -> QuestionDiscoveryRun | AppError:
        async with async_session_factory() as session:
            try:
                return await question_discovery.create_run(session, profile_id, payload)
            except AppError as exc:
                await session.rollback()
                return exc

    results = await asyncio.wait_for(
        asyncio.gather(*(create_once() for _ in range(attempt_count))),
        timeout=10,
    )
    created = [result for result in results if isinstance(result, QuestionDiscoveryRun)]
    rejected = [result for result in results if isinstance(result, AppError)]

    assert len(created) == question_discovery.settings.discovery_max_concurrent_runs_per_profile
    assert len(rejected) == 1
    assert rejected[0].code == "discovery_run_concurrency_limit"

    async with async_session_factory() as session:
        active_count = await session.scalar(
            select(func.count())
            .select_from(QuestionDiscoveryRun)
            .where(
                QuestionDiscoveryRun.profile_id == profile_id,
                QuestionDiscoveryRun.status.in_(question_discovery.ACTIVE_RUN_STATUSES),
            )
        )
        job_count = await session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.profile_id == profile_id)
        )

    assert int(active_count or 0) == len(created)
    assert int(job_count or 0) == len(created)


@pytest.mark.asyncio
async def test_url_run_keeps_raw_pasted_urls_out_of_public_responses() -> None:
    raw_url = "https://public.example.invalid/interview?token=private-value"
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        connector_id = await create_connector(client)
        created = await client.post(
            "/api/question-discoveries",
            json={
                "connector_id": connector_id,
                "source_mode": "urls",
                "urls": [raw_url],
            },
        )
        retrieved = await client.get(f"/api/question-discoveries/{created.json()['id']}")

    assert created.status_code == 202
    for response in (created, retrieved):
        snapshot = response.json()["query_snapshot"]
        assert snapshot["url_count"] == 1
        assert "urls" not in snapshot
        assert raw_url not in response.text


@pytest.mark.asyncio
async def test_candidate_evidence_is_scoped_to_the_requested_run_and_candidate() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        connector_id = await create_connector(client)
        created = await client.post(
            "/api/question-discoveries",
            json={
                "connector_id": connector_id,
                "source_mode": "search",
                "query": "LLM retrieval interview questions",
            },
        )
        assert created.status_code == 202
        run_id = uuid.UUID(created.json()["id"])

        async with async_session_factory() as session:
            run = await session.get(QuestionDiscoveryRun, run_id)
            assert run is not None
            source = QuestionDiscoverySource(
                profile_id=run.profile_id,
                run_id=run.id,
                normalized_url="https://search.acme.cn/outbound/retrieval-notes",
                final_url="https://interview.acme.cn/retrieval-notes",
                title="Retrieval interview notes",
                domain="interview.acme.cn",
                source_category="community_notes",
                status=DiscoverySourceStatus.FETCHED,
                excerpt="Evaluate retrieval with offline metrics and failure analysis.",
            )
            candidate = QuestionDiscoveryCandidate(
                profile_id=run.profile_id,
                run_id=run.id,
                prompt="How would you evaluate retrieval quality before launch?",
                question_type=QuestionType.SYSTEM_DESIGN,
                difficulty=Difficulty.ADVANCED,
                content_hash="a" * 64,
            )
            other_run = QuestionDiscoveryRun(
                profile_id=run.profile_id,
                connector_id=run.connector_id,
                connector_configuration_version=run.connector_configuration_version,
                source_mode=DiscoverySourceMode.SEARCH,
            )
            session.add_all([source, candidate, other_run])
            await session.flush()
            session.add(
                QuestionDiscoveryCandidateEvidence(
                    profile_id=run.profile_id,
                    run_id=run.id,
                    candidate_id=candidate.id,
                    source_id=source.id,
                    excerpt="offline metrics and failure analysis",
                    confidence=0.8,
                    evidence_hash="b" * 64,
                )
            )
            other_candidate = QuestionDiscoveryCandidate(
                profile_id=run.profile_id,
                run_id=other_run.id,
                prompt="Which retrieval metric would you optimize first?",
                question_type=QuestionType.OPEN_ENDED,
                difficulty=Difficulty.INTERMEDIATE,
                content_hash="c" * 64,
            )
            session.add(other_candidate)
            await session.commit()

        evidence = await client.get(
            f"/api/question-discoveries/{run_id}/candidates/{candidate.id}/evidence"
        )
        cross_run = await client.get(
            f"/api/question-discoveries/{run_id}/candidates/{other_candidate.id}/evidence"
        )
        unknown_run = await client.get(
            f"/api/question-discoveries/{uuid.uuid4()}/candidates/{candidate.id}/evidence"
        )

    assert evidence.status_code == 200
    evidence_body = evidence.json()
    assert len(evidence_body) == 1
    assert evidence_body[0]["run_id"] == str(run_id)
    assert evidence_body[0]["candidate_id"] == str(candidate.id)
    assert evidence_body[0]["source_id"] == str(source.id)
    assert evidence_body[0]["source_title"] == "Retrieval interview notes"
    assert evidence_body[0]["normalized_url"] == "https://interview.acme.cn/retrieval-notes"
    assert evidence_body[0]["source_domain"] == "interview.acme.cn"
    assert evidence_body[0]["source_category"] == "community_notes"
    assert evidence_body[0]["excerpt"] == "offline metrics and failure analysis"
    assert evidence_body[0]["source_locator"] is None
    assert evidence_body[0]["confidence"] == 0.8
    assert cross_run.status_code == 404
    assert cross_run.json()["code"] == "discovery_candidate_not_found"
    assert unknown_run.status_code == 404
    assert unknown_run.json()["code"] == "question_discovery_not_found"


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_only_terminal_runs_can_be_deleted() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        connector_id = await create_connector(client)
        created = await client.post(
            "/api/question-discoveries",
            json={
                "connector_id": connector_id,
                "source_mode": "search",
                "query": "LLM 应用面试题",
            },
        )
        run_id = uuid.UUID(created.json()["id"])
        cancelled = await client.post(f"/api/question-discoveries/{run_id}/cancel")
        repeated = await client.post(f"/api/question-discoveries/{run_id}/cancel")
        deleting_active = await client.delete(f"/api/question-discoveries/{run_id}")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancel_requested"
    assert repeated.status_code == 200
    assert deleting_active.status_code == 409

    async with async_session_factory() as session:
        run = await session.get(QuestionDiscoveryRun, run_id)
        assert run is not None
        run.status = DiscoveryRunStatus.CANCELLED
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        deleted = await client.delete(f"/api/question-discoveries/{run_id}")

    assert deleted.status_code == 204
