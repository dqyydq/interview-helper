import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.agents.question_researcher import (
    ResearcherCandidate,
    ResearcherEvidence,
    ResearcherResult,
)
from app.api.errors import AppError
from app.core.crypto import SecretDecryptionError
from app.db.models.common import (
    Difficulty,
    DiscoveryProviderType,
    DiscoverySourceMode,
    DiscoverySourceStatus,
    ProviderType,
    QuestionType,
)
from app.db.models.discovery import (
    DiscoveryConnector,
    QuestionDiscoveryCandidate,
    QuestionDiscoveryCandidateEvidence,
    QuestionDiscoveryRun,
    QuestionDiscoverySource,
)
from app.db.models.model_connection import ModelConnection
from app.db.models.profile import UserProfile
from app.db.session import async_session_factory, engine
from app.services import question_curation


async def clear_curation_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(QuestionDiscoveryCandidateEvidence))
        await session.execute(delete(QuestionDiscoveryCandidate))
        await session.execute(delete(QuestionDiscoverySource))
        await session.execute(delete(QuestionDiscoveryRun))
        await session.execute(delete(ModelConnection))
        await session.execute(delete(DiscoveryConnector))
        await session.execute(delete(UserProfile))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def isolated_curation_rows():
    await clear_curation_rows()
    yield
    await clear_curation_rows()
    await engine.dispose()


async def make_run_with_source() -> tuple[QuestionDiscoveryRun, QuestionDiscoverySource]:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Curation profile")
        session.add(profile)
        await session.flush()
        connector = DiscoveryConnector(
            profile_id=profile.id,
            name="Curation connector",
            provider_type=DiscoveryProviderType.TAVILY,
            encrypted_api_key="encrypted",
            capabilities={"supports_extract": True, "safe_extract": True},
        )
        session.add(connector)
        await session.flush()
        run = QuestionDiscoveryRun(
            profile_id=profile.id,
            connector_id=connector.id,
            connector_configuration_version=1,
            source_mode=DiscoverySourceMode.SEARCH,
            query_snapshot={
                "company": "byte-dance",
                "role": "LLM application engineer",
                "urls": ["https://never-send-this.example.test/private"],
            },
        )
        session.add(run)
        await session.flush()
        source = QuestionDiscoverySource(
            profile_id=profile.id,
            run_id=run.id,
            normalized_url="https://public.example.test/interview",
            final_url="https://public.example.test/interview",
            title="Interview notes",
            domain="public.example.test",
            source_category="community_notes",
            status=DiscoverySourceStatus.FETCHED,
            excerpt="The source says candidates should discuss retrieval evaluation and failures.",
        )
        session.add(source)
        await session.commit()
        await session.refresh(run)
        await session.refresh(source)
        return run, source


class CloseableProvider:
    async def aclose(self) -> None:
        return None


async def make_researcher_connection(profile_id: uuid.UUID) -> ModelConnection:
    async with async_session_factory() as session:
        connection = ModelConnection(
            profile_id=profile_id,
            name="Curation researcher",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            encrypted_api_key="encrypted",
            model_name="researcher-model",
            context_window_tokens=32_768,
            max_output_tokens=2_048,
        )
        session.add(connection)
        await session.commit()
        await session.refresh(connection)
        return connection


@pytest.mark.asyncio
async def test_curation_persists_only_source_grounded_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, source = await make_run_with_source()
    connection = await make_researcher_connection(run.profile_id)

    async def resolve(*_args: object) -> object:
        return connection

    async def curate(_provider, sources, *, query_context, **_kwargs):
        assert [item.source_id for item in sources] == [source.id]
        assert "urls" not in query_context
        return ResearcherResult(
            candidates=(
                ResearcherCandidate(
                    prompt="How would you evaluate retrieval failures before launch?",
                    question_type=QuestionType.SYSTEM_DESIGN,
                    difficulty=Difficulty.ADVANCED,
                    suggested_tags=("RAG",),
                    suggested_roles=("LLM application engineer",),
                    suggested_skills=("retrieval",),
                    applicable_companies=("byte-dance",),
                    applicable_rounds=(),
                    reference_points=("Explain metrics",),
                    follow_up_suggestions=("How would you debug a regression?",),
                    matching_reason="Tests grounded retrieval evaluation.",
                    confidence=0.9,
                    evidence=(
                        ResearcherEvidence(
                            source_id=source.id,
                            excerpt="discuss retrieval evaluation and failures",
                            source_locator=None,
                            confidence=0.8,
                        ),
                    ),
                ),
            ),
            input_source_count=1,
            input_excerpt_characters=70,
        )

    monkeypatch.setattr(question_curation, "resolve_explicit_role_connection", resolve)
    monkeypatch.setattr(
        question_curation,
        "build_provider",
        lambda _connection: CloseableProvider(),
    )
    monkeypatch.setattr(question_curation, "curate_questions", curate)

    async with async_session_factory() as session:
        database_run = await session.get(QuestionDiscoveryRun, run.id)
        assert database_run is not None
        outcome = await question_curation.curate_discovery_run(session, database_run)
        await session.commit()
        candidate = await session.scalar(
            select(QuestionDiscoveryCandidate).where(QuestionDiscoveryCandidate.run_id == run.id)
        )
        evidence = await session.scalar(
            select(QuestionDiscoveryCandidateEvidence).where(
                QuestionDiscoveryCandidateEvidence.run_id == run.id
            )
        )

    assert outcome.status == "curated"
    assert outcome.candidate_count == 1
    assert candidate is not None
    assert candidate.researcher_connection_id == connection.id
    assert candidate.researcher_model_name == "researcher-model"
    assert evidence is not None
    assert evidence.source_id == source.id
    assert evidence.excerpt == "discuss retrieval evaluation and failures"


@pytest.mark.asyncio
async def test_curation_keeps_sources_without_using_interviewer_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _ = await make_run_with_source()

    async def unbound(*_args: object) -> object:
        raise AppError(code="model_role_unbound", message="missing", status_code=409)

    monkeypatch.setattr(question_curation, "resolve_explicit_role_connection", unbound)
    monkeypatch.setattr(
        question_curation,
        "build_provider",
        lambda _connection: pytest.fail("an unbound researcher must not build a fallback provider"),
    )

    async with async_session_factory() as session:
        database_run = await session.get(QuestionDiscoveryRun, run.id)
        assert database_run is not None
        outcome = await question_curation.curate_discovery_run(session, database_run)

    assert outcome.status == "skipped"
    assert outcome.error_code == "discovery_researcher_unbound"


@pytest.mark.asyncio
async def test_curation_degrades_when_researcher_credential_cannot_be_decrypted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _ = await make_run_with_source()
    connection = await make_researcher_connection(run.profile_id)

    async def resolve(*_args: object) -> object:
        return connection

    def unavailable_provider(*_args: object) -> object:
        raise SecretDecryptionError("credential cannot be decrypted")

    monkeypatch.setattr(question_curation, "resolve_explicit_role_connection", resolve)
    monkeypatch.setattr(question_curation, "build_provider", unavailable_provider)

    async with async_session_factory() as session:
        database_run = await session.get(QuestionDiscoveryRun, run.id)
        assert database_run is not None
        outcome = await question_curation.curate_discovery_run(session, database_run)
        candidate_count = await session.scalar(
            select(QuestionDiscoveryCandidate)
            .where(QuestionDiscoveryCandidate.run_id == run.id)
            .limit(1)
        )

    assert outcome.status == "failed"
    assert outcome.error_code == "discovery_researcher_unavailable"
    assert candidate_count is None
