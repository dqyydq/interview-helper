import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.models.common import (
    DiscoveryProviderType,
    DiscoveryRunStatus,
    DiscoverySourceMode,
    DiscoverySourceStatus,
    JobStatus,
    JobType,
    utc_now,
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
from app.discovery.providers.base import (
    ExtractedSource,
    ExtractRequest,
    ExtractResponse,
    SearchProviderCapabilities,
    SearchQuery,
    SearchResult,
)
from app.discovery.url_policy import DomainPolicy, URLPolicy
from app.services.question_curation import CurationOutcome
from app.workers import discovery_jobs


class FakeSearchProvider:
    capabilities = SearchProviderCapabilities(
        supports_domain_filters=True,
        supports_extract=True,
        safe_extract=True,
    )

    def __init__(self) -> None:
        self.search_requests: list[SearchQuery] = []
        self.extract_requests: list[ExtractRequest] = []
        self.closed = False

    async def search(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        self.search_requests.append(query)
        return (
            SearchResult(
                url="https://interview.acme.cn/interview",
                title="Public interview notes",
                content="short provider snippet",
            ),
        )

    async def extract(self, request: ExtractRequest) -> ExtractResponse:
        self.extract_requests.append(request)
        return ExtractResponse(
            sources=(
                ExtractedSource(
                    url="https://interview.acme.cn/interview",
                    canonical_url="https://interview.acme.cn/interview",
                    title="Public interview notes",
                    content="Detailed source text about retrieval evaluation and failure analysis.",
                ),
            )
        )

    async def health_check(self):  # pragma: no cover - the worker does not health-check per run
        raise AssertionError("health checks belong to connector settings, not a discovery run")

    async def aclose(self) -> None:
        self.closed = True


class MixedSourceProvider(FakeSearchProvider):
    def __init__(self, urls: tuple[str, ...]) -> None:
        super().__init__()
        self.urls = urls

    async def search(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        self.search_requests.append(query)
        return tuple(
            SearchResult(
                url=url,
                title=f"Source {index}",
                content=f"Provider snippet {index}",
            )
            for index, url in enumerate(self.urls, start=1)
        )

    async def extract(self, request: ExtractRequest) -> ExtractResponse:
        self.extract_requests.append(request)
        return ExtractResponse(
            sources=tuple(
                ExtractedSource(
                    url=url,
                    canonical_url=url,
                    title="Public interview notes",
                    content="Detailed source text about retrieval evaluation.",
                )
                for url in request.urls
            )
        )


async def clear_discovery_worker_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(BackgroundJob))
        await session.execute(delete(QuestionDiscoveryCandidateEvidence))
        await session.execute(delete(QuestionDiscoveryCandidate))
        await session.execute(delete(QuestionDiscoverySource))
        await session.execute(delete(QuestionDiscoveryRun))
        await session.execute(delete(DiscoveryConnector))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_discovery_worker_rows():
    await clear_discovery_worker_rows()
    yield
    await clear_discovery_worker_rows()
    await engine.dispose()


async def make_queued_search_job() -> tuple[uuid.UUID, uuid.UUID]:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Discovery worker profile")
        session.add(profile)
        await session.flush()
        connector = DiscoveryConnector(
            profile_id=profile.id,
            name="Discovery worker connector",
            provider_type=DiscoveryProviderType.TAVILY,
            encrypted_api_key="encrypted",
            capabilities={
                "supports_domain_filters": True,
                "supports_extract": True,
                "safe_extract": True,
            },
        )
        session.add(connector)
        await session.flush()
        run = QuestionDiscoveryRun(
            profile_id=profile.id,
            connector_id=connector.id,
            connector_configuration_version=1,
            source_mode=DiscoverySourceMode.SEARCH,
            query_snapshot={
                "source_mode": "search",
                "search_query": "LLM retrieval interview questions",
                "country": None,
                "domain_policy": {
                    "allow_domains": [],
                    "deny_domains": [],
                },
            },
        )
        session.add(run)
        await session.flush()
        job = BackgroundJob(
            profile_id=profile.id,
            job_type=JobType.QUESTION_DISCOVERY,
            status=JobStatus.QUEUED,
            payload={"run_id": str(run.id)},
            idempotency_key=f"test-discovery:{run.id}",
            max_attempts=1,
        )
        session.add(job)
        await session.commit()
        return run.id, job.id


@pytest.mark.asyncio
async def test_worker_retrieves_bounded_source_cards_then_runs_safe_curation_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, job_id = await make_queued_search_job()
    provider = FakeSearchProvider()
    curation_calls: list[uuid.UUID] = []

    monkeypatch.setattr(
        discovery_jobs.discovery_connectors,
        "build_search_provider",
        lambda _connector: provider,
    )
    monkeypatch.setattr(
        discovery_jobs,
        "_url_policy_for_run",
        lambda _run: URLPolicy(dns_resolver=lambda _host: ("8.8.8.8",)),
    )

    async def curate(_session, run, *, cancellation_check):
        await cancellation_check()
        curation_calls.append(run.id)
        return CurationOutcome(status="skipped", candidate_count=0)

    monkeypatch.setattr(discovery_jobs.question_curation, "curate_discovery_run", curate)

    assert await discovery_jobs.run_once("discovery-test-worker") is True

    async with async_session_factory() as session:
        run = await session.get(QuestionDiscoveryRun, run_id)
        job = await session.get(BackgroundJob, job_id)
        sources = list(
            (
                await session.scalars(
                    select(QuestionDiscoverySource).where(QuestionDiscoverySource.run_id == run_id)
                )
            ).all()
        )

    assert run is not None
    assert job is not None
    assert run.status == DiscoveryRunStatus.SUCCEEDED, (run.error_code, job.error_code)
    assert run.source_count == 1
    assert run.candidate_count == 0
    assert job.status == JobStatus.COMPLETED
    assert job.result["candidate_count"] == 0
    assert curation_calls == [run_id]
    assert provider.closed is True
    assert provider.search_requests[0].max_results <= 20
    assert provider.extract_requests[0].urls == ("https://interview.acme.cn/interview",)
    assert len(sources) == 1
    assert sources[0].excerpt == (
        "Detailed source text about retrieval evaluation and failure analysis."
    )


@pytest.mark.asyncio
async def test_worker_keeps_sources_and_marks_run_partial_when_curation_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, job_id = await make_queued_search_job()
    provider = FakeSearchProvider()

    monkeypatch.setattr(
        discovery_jobs.discovery_connectors,
        "build_search_provider",
        lambda _connector: provider,
    )
    monkeypatch.setattr(
        discovery_jobs,
        "_url_policy_for_run",
        lambda _run: URLPolicy(dns_resolver=lambda _host: ("8.8.8.8",)),
    )

    async def degrade(*_args: object, **_kwargs: object) -> CurationOutcome:
        return CurationOutcome(
            status="failed",
            candidate_count=0,
            error_code="discovery_researcher_unavailable",
            error_summary="Researcher unavailable.",
        )

    monkeypatch.setattr(discovery_jobs.question_curation, "curate_discovery_run", degrade)

    assert await discovery_jobs.run_once("discovery-test-worker") is True

    async with async_session_factory() as session:
        run = await session.get(QuestionDiscoveryRun, run_id)
        job = await session.get(BackgroundJob, job_id)
        source_count = await session.scalar(
            select(QuestionDiscoverySource).where(QuestionDiscoverySource.run_id == run_id).limit(1)
        )

    assert run is not None
    assert job is not None
    assert run.status == DiscoveryRunStatus.PARTIAL
    assert run.error_code == "discovery_researcher_unavailable"
    assert job.status == JobStatus.COMPLETED
    assert source_count is not None
    assert provider.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("source_mode", (DiscoverySourceMode.SEARCH, DiscoverySourceMode.URLS))
async def test_worker_caps_mixed_valid_and_blocked_source_cards_across_input_modes(
    monkeypatch: pytest.MonkeyPatch,
    source_mode: DiscoverySourceMode,
) -> None:
    run_id, _ = await make_queued_search_job()
    urls = (
        "https://blocked.acme.cn/one",
        "https://interview.acme.cn/one",
        "https://blocked.acme.cn/two",
        "https://interview.acme.cn/two",
    )
    provider = MixedSourceProvider(urls)

    async with async_session_factory() as session:
        run = await session.get(QuestionDiscoveryRun, run_id)
        assert run is not None
        run.source_mode = source_mode
        run.query_snapshot = {
            "source_mode": source_mode.value,
            "domain_policy": {
                "allow_domains": [],
                "deny_domains": ["blocked.acme.cn"],
            },
            **(
                {"search_query": "LLM retrieval interview questions", "country": None}
                if source_mode is DiscoverySourceMode.SEARCH
                else {"urls": list(urls)}
            ),
        }
        run.touch()
        await session.commit()

    monkeypatch.setattr(
        discovery_jobs.discovery_connectors,
        "build_search_provider",
        lambda _connector: provider,
    )
    monkeypatch.setattr(discovery_jobs.discovery_service, "max_sources", lambda: 2)
    monkeypatch.setattr(
        discovery_jobs,
        "_url_policy_for_run",
        lambda _run: URLPolicy(
            domain_policy=DomainPolicy(deny_domains=("blocked.acme.cn",)),
            dns_resolver=lambda _host: ("8.8.8.8",),
        ),
    )

    async def skip_curation(*_args: object, **_kwargs: object) -> CurationOutcome:
        return CurationOutcome(status="skipped", candidate_count=0)

    monkeypatch.setattr(discovery_jobs.question_curation, "curate_discovery_run", skip_curation)

    assert await discovery_jobs.run_once("discovery-test-worker") is True

    async with async_session_factory() as session:
        sources = list(
            (
                await session.scalars(
                    select(QuestionDiscoverySource)
                    .where(QuestionDiscoverySource.run_id == run_id)
                    .order_by(QuestionDiscoverySource.created_at, QuestionDiscoverySource.id)
                )
            ).all()
        )

    assert len(sources) == 2
    assert {source.status for source in sources} == {
        DiscoverySourceStatus.BLOCKED,
        DiscoverySourceStatus.FETCHED,
    }
    assert provider.extract_requests[0].urls == ("https://interview.acme.cn/one",)


@pytest.mark.asyncio
async def test_worker_recovers_stale_discovery_job_before_claiming_queued_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_run_id, stale_job_id = await make_queued_search_job()
    fresh_run_id, fresh_job_id = await make_queued_search_job()
    stale_lock = utc_now() - timedelta(seconds=discovery_jobs._discovery_job_lease_seconds() + 1)

    async with async_session_factory() as session:
        stale_job = await session.get(BackgroundJob, stale_job_id)
        stale_run = await session.get(QuestionDiscoveryRun, stale_run_id)
        assert stale_job is not None
        assert stale_run is not None
        stale_job.status = JobStatus.RUNNING
        stale_job.locked_at = stale_lock
        stale_job.locked_by = "lost-discovery-worker"
        stale_job.progress = 0.4
        stale_job.touch(at=stale_lock)
        stale_run.status = DiscoveryRunStatus.RUNNING
        stale_run.stage = "extracting"
        stale_run.progress = 0.4
        stale_run.touch(at=stale_lock)
        await session.commit()

    provider = FakeSearchProvider()
    monkeypatch.setattr(
        discovery_jobs.discovery_connectors,
        "build_search_provider",
        lambda _connector: provider,
    )
    monkeypatch.setattr(
        discovery_jobs,
        "_url_policy_for_run",
        lambda _run: URLPolicy(dns_resolver=lambda _host: ("8.8.8.8",)),
    )

    async def skip_curation(*_args: object, **_kwargs: object) -> CurationOutcome:
        return CurationOutcome(status="skipped", candidate_count=0)

    monkeypatch.setattr(discovery_jobs.question_curation, "curate_discovery_run", skip_curation)

    assert await discovery_jobs.run_once("replacement-discovery-worker") is True

    async with async_session_factory() as session:
        stale_job = await session.get(BackgroundJob, stale_job_id)
        stale_run = await session.get(QuestionDiscoveryRun, stale_run_id)
        fresh_job = await session.get(BackgroundJob, fresh_job_id)
        fresh_run = await session.get(QuestionDiscoveryRun, fresh_run_id)

    assert stale_job is not None
    assert stale_run is not None
    assert fresh_job is not None
    assert fresh_run is not None
    assert stale_job.status == JobStatus.FAILED
    assert stale_job.error_code == "discovery_worker_lost"
    assert stale_job.locked_at is None
    assert stale_job.locked_by is None
    assert stale_run.status == DiscoveryRunStatus.FAILED
    assert stale_run.error_code == "discovery_worker_lost"
    assert stale_run.completed_at is not None
    assert fresh_job.status == JobStatus.COMPLETED
    assert fresh_run.status == DiscoveryRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_worker_does_not_recover_fresh_running_discovery_job() -> None:
    run_id, job_id = await make_queued_search_job()
    lock_time = utc_now()

    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, job_id)
        run = await session.get(QuestionDiscoveryRun, run_id)
        assert job is not None
        assert run is not None
        job.status = JobStatus.RUNNING
        job.locked_at = lock_time
        job.locked_by = "active-discovery-worker"
        job.progress = 0.4
        job.touch(at=lock_time)
        run.status = DiscoveryRunStatus.RUNNING
        run.stage = "extracting"
        run.progress = 0.4
        run.touch(at=lock_time)
        await session.commit()

    assert await discovery_jobs.run_once("replacement-discovery-worker") is False

    async with async_session_factory() as session:
        job = await session.get(BackgroundJob, job_id)
        run = await session.get(QuestionDiscoveryRun, run_id)

    assert job is not None
    assert run is not None
    assert job.status == JobStatus.RUNNING
    assert job.error_code is None
    assert job.locked_at == lock_time
    assert job.locked_by == "active-discovery-worker"
    assert run.status == DiscoveryRunStatus.RUNNING
    assert run.error_code is None
