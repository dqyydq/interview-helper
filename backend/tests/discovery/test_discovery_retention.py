import uuid
from dataclasses import dataclass
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.models.common import (
    Difficulty,
    DiscoveryImportStatus,
    DiscoveryProviderType,
    DiscoveryRunStatus,
    DiscoverySourceMode,
    DiscoverySourceStatus,
    QuestionStatus,
    QuestionType,
    SourceType,
    utc_now,
)
from app.db.models.discovery import (
    DiscoveryConnector,
    QuestionDiscoveryCandidate,
    QuestionDiscoveryCandidateEvidence,
    QuestionDiscoveryImport,
    QuestionDiscoveryRun,
    QuestionDiscoverySource,
    QuestionSourceProvenance,
)
from app.db.models.profile import UserProfile
from app.db.models.question import Question, QuestionBank
from app.db.session import async_session_factory, engine
from app.services.discovery_retention import cleanup_expired_discovery_runs
from app.services.questions import prompt_hash


@dataclass(frozen=True)
class RetentionFixture:
    profile_id: uuid.UUID
    expired_run_id: uuid.UUID
    active_run_id: uuid.UUID
    source_id: uuid.UUID
    candidate_id: uuid.UUID
    evidence_id: uuid.UUID
    import_id: uuid.UUID
    provenance_id: uuid.UUID
    question_id: uuid.UUID


async def _clear_retention_rows() -> None:
    async with async_session_factory() as session:
        profile_ids = list(
            (
                await session.scalars(
                    select(UserProfile.id).where(UserProfile.display_name.like("Retention test %"))
                )
            ).all()
        )
        for profile_id in profile_ids:
            await session.execute(
                delete(QuestionSourceProvenance).where(
                    QuestionSourceProvenance.profile_id == profile_id
                )
            )
            await session.execute(
                delete(QuestionDiscoveryImport).where(
                    QuestionDiscoveryImport.profile_id == profile_id
                )
            )
            await session.execute(delete(QuestionBank).where(QuestionBank.profile_id == profile_id))
            await session.execute(
                delete(QuestionDiscoveryRun).where(QuestionDiscoveryRun.profile_id == profile_id)
            )
            await session.execute(
                delete(DiscoveryConnector).where(DiscoveryConnector.profile_id == profile_id)
            )
            await session.execute(delete(UserProfile).where(UserProfile.id == profile_id))
        await session.commit()


async def _make_fixture() -> RetentionFixture:
    now = utc_now()
    async with async_session_factory() as session:
        profile = UserProfile(display_name=f"Retention test {uuid.uuid4()}")
        session.add(profile)
        await session.flush()
        connector = DiscoveryConnector(
            profile_id=profile.id,
            name=f"Retention connector {uuid.uuid4()}",
            provider_type=DiscoveryProviderType.TAVILY,
        )
        bank = QuestionBank(profile_id=profile.id, name=f"Retention bank {uuid.uuid4()}")
        session.add_all([connector, bank])
        await session.flush()

        expired_run = QuestionDiscoveryRun(
            profile_id=profile.id,
            connector_id=connector.id,
            connector_configuration_version=connector.configuration_version,
            source_mode=DiscoverySourceMode.SEARCH,
            status=DiscoveryRunStatus.SUCCEEDED,
            completed_at=now - timedelta(days=31),
            expires_at=now - timedelta(seconds=1),
        )
        active_run = QuestionDiscoveryRun(
            profile_id=profile.id,
            connector_id=connector.id,
            connector_configuration_version=connector.configuration_version,
            source_mode=DiscoverySourceMode.SEARCH,
            status=DiscoveryRunStatus.RUNNING,
            expires_at=now - timedelta(seconds=1),
        )
        session.add_all([expired_run, active_run])
        await session.flush()

        source = QuestionDiscoverySource(
            profile_id=profile.id,
            run_id=expired_run.id,
            normalized_url="https://interview.acme.cn/retention-notes",
            final_url="https://interview.acme.cn/retention-notes",
            title="Retention source",
            domain="interview.acme.cn",
            source_category="community_notes",
            status=DiscoverySourceStatus.FETCHED,
            fetched_at=now,
            excerpt="A bounded source excerpt for retention cleanup coverage.",
            attribution={"source_kind": "community_notes"},
            expires_at=expired_run.expires_at,
        )
        candidate_prompt = "How would you validate retrieval quality before release?"
        candidate = QuestionDiscoveryCandidate(
            profile_id=profile.id,
            run_id=expired_run.id,
            prompt=candidate_prompt,
            question_type=QuestionType.SYSTEM_DESIGN,
            difficulty=Difficulty.ADVANCED,
            content_hash=prompt_hash(candidate_prompt),
            expires_at=expired_run.expires_at,
        )
        session.add_all([source, candidate])
        await session.flush()
        evidence = QuestionDiscoveryCandidateEvidence(
            profile_id=profile.id,
            run_id=expired_run.id,
            candidate_id=candidate.id,
            source_id=source.id,
            excerpt="bounded source excerpt",
            evidence_hash="a" * 64,
            expires_at=expired_run.expires_at,
        )
        question = Question(
            bank_id=bank.id,
            prompt=candidate_prompt,
            question_type=QuestionType.SYSTEM_DESIGN,
            difficulty=Difficulty.ADVANCED,
            status=QuestionStatus.DRAFT,
            source_type=SourceType.LINK_IMPORT,
            normalized_hash=prompt_hash(candidate_prompt),
        )
        session.add_all([evidence, question])
        await session.flush()
        import_audit = QuestionDiscoveryImport(
            profile_id=profile.id,
            candidate_id=candidate.id,
            candidate_content_hash=candidate.content_hash,
            bank_id=bank.id,
            question_id=question.id,
            idempotency_key=f"retention-import-{uuid.uuid4()}",
            request_hash="b" * 64,
            candidate_revision=candidate.candidate_revision,
            status=DiscoveryImportStatus.SUCCEEDED,
            completed_at=now,
        )
        provenance = QuestionSourceProvenance(
            profile_id=profile.id,
            question_id=question.id,
            discovery_run_id=expired_run.id,
            candidate_id=candidate.id,
            source_title=source.title,
            normalized_url=source.normalized_url,
            source_domain=source.domain,
            source_category=source.source_category,
            fetched_at=now,
            excerpt="bounded source excerpt",
            evidence_hash=evidence.evidence_hash,
            attribution=source.attribution,
        )
        session.add_all([import_audit, provenance])
        await session.commit()
        return RetentionFixture(
            profile_id=profile.id,
            expired_run_id=expired_run.id,
            active_run_id=active_run.id,
            source_id=source.id,
            candidate_id=candidate.id,
            evidence_id=evidence.id,
            import_id=import_audit.id,
            provenance_id=provenance.id,
            question_id=question.id,
        )


@pytest_asyncio.fixture
async def retention_fixture() -> RetentionFixture:
    await _clear_retention_rows()
    fixture = await _make_fixture()
    yield fixture
    await _clear_retention_rows()
    await engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_hard_deletes_only_expired_terminal_runs_and_keeps_import_history(
    retention_fixture: RetentionFixture,
) -> None:
    async with async_session_factory() as session:
        removed = await cleanup_expired_discovery_runs(session, now=utc_now())
        expired_run = await session.get(QuestionDiscoveryRun, retention_fixture.expired_run_id)
        active_run = await session.get(QuestionDiscoveryRun, retention_fixture.active_run_id)
        source = await session.get(QuestionDiscoverySource, retention_fixture.source_id)
        candidate = await session.get(QuestionDiscoveryCandidate, retention_fixture.candidate_id)
        evidence = await session.get(
            QuestionDiscoveryCandidateEvidence,
            retention_fixture.evidence_id,
        )
        import_audit = await session.get(QuestionDiscoveryImport, retention_fixture.import_id)
        provenance = await session.get(QuestionSourceProvenance, retention_fixture.provenance_id)
        question = await session.get(Question, retention_fixture.question_id)

    assert removed == 1
    assert expired_run is None
    assert active_run is not None
    assert source is None
    assert candidate is None
    assert evidence is None
    assert import_audit is not None
    assert import_audit.candidate_id is None
    assert provenance is not None
    assert provenance.discovery_run_id is None
    assert provenance.candidate_id is None
    assert question is not None
