import hashlib
import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.api.errors import AppError
from app.db.models.common import (
    Difficulty,
    DiscoveryCandidateStatus,
    DiscoveryImportStatus,
    DiscoveryProviderType,
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
from app.db.models.question import (
    Question,
    QuestionBank,
    QuestionTag,
    QuestionTagLink,
    QuestionVariant,
)
from app.db.session import async_session_factory, engine
from app.services.discovery_imports import (
    DiscoveryImportItem,
    DiscoveryImportRequest,
    import_discovery_candidates,
)
from app.services.questions import prompt_hash


@dataclass(frozen=True)
class CandidateFixture:
    id: uuid.UUID
    revision: int
    prompt: str


@dataclass(frozen=True)
class ImportFixture:
    profile_id: uuid.UUID
    bank_id: uuid.UUID
    run_id: uuid.UUID
    source_id: uuid.UUID
    candidates: tuple[CandidateFixture, ...]


def _evidence_hash(candidate_hash: str, source_id: uuid.UUID, excerpt: str) -> str:
    return hashlib.sha256(f"{candidate_hash}:{source_id}:{excerpt}".encode()).hexdigest()


async def _make_fixture(*, candidate_count: int = 2) -> ImportFixture:
    async with async_session_factory() as session:
        profile = UserProfile(display_name=f"Import test {uuid.uuid4()}")
        session.add(profile)
        await session.flush()

        connector = DiscoveryConnector(
            profile_id=profile.id,
            name=f"Import connector {uuid.uuid4()}",
            provider_type=DiscoveryProviderType.TAVILY,
        )
        session.add(connector)
        await session.flush()

        run = QuestionDiscoveryRun(
            profile_id=profile.id,
            connector_id=connector.id,
            connector_configuration_version=connector.configuration_version,
            source_mode=DiscoverySourceMode.SEARCH,
        )
        bank = QuestionBank(profile_id=profile.id, name=f"Import bank {uuid.uuid4()}")
        session.add_all([run, bank])
        await session.flush()

        source = QuestionDiscoverySource(
            profile_id=profile.id,
            run_id=run.id,
            normalized_url="https://search.example.test/outbound/llm-interview-notes",
            final_url="https://public.example.test/llm-interview-notes",
            title="Public LLM interview notes",
            domain="public.example.test",
            source_category="community_notes",
            status=DiscoverySourceStatus.FETCHED,
            fetched_at=utc_now(),
            excerpt="Candidates discuss retrieval evaluation, failures, and trade-offs.",
            attribution={"source_kind": "community_notes", "official": False},
        )
        session.add(source)
        await session.flush()

        candidates: list[CandidateFixture] = []
        for index in range(candidate_count):
            prompt = f"How would you evaluate retrieval quality before release? Variant {index}"
            content_hash = prompt_hash(prompt)
            candidate = QuestionDiscoveryCandidate(
                profile_id=profile.id,
                run_id=run.id,
                prompt=prompt,
                question_type=QuestionType.SYSTEM_DESIGN,
                difficulty=Difficulty.ADVANCED,
                suggested_tags=["RAG", "evaluation"],
                suggested_roles=["LLM application engineer"],
                suggested_skills=["retrieval"],
                applicable_companies=["byte-dance"],
                applicable_rounds=["technical-depth"],
                reference_points=["Offline metrics", "Failure analysis"],
                follow_up_suggestions=["How would you detect a regression?"],
                matching_reason="Tests retrieval evaluation and trade-off reasoning.",
                confidence=0.9,
                content_hash=content_hash,
            )
            session.add(candidate)
            await session.flush()
            evidence_excerpt = f"retrieval evaluation evidence {index}"
            session.add(
                QuestionDiscoveryCandidateEvidence(
                    profile_id=profile.id,
                    run_id=run.id,
                    candidate_id=candidate.id,
                    source_id=source.id,
                    excerpt=evidence_excerpt,
                    evidence_hash=_evidence_hash(content_hash, source.id, evidence_excerpt),
                )
            )
            candidates.append(
                CandidateFixture(
                    id=candidate.id,
                    revision=candidate.candidate_revision,
                    prompt=prompt,
                )
            )
        await session.commit()
        return ImportFixture(
            profile_id=profile.id,
            bank_id=bank.id,
            run_id=run.id,
            source_id=source.id,
            candidates=tuple(candidates),
        )


async def _clear_fixture(profile_id: uuid.UUID) -> None:
    async with async_session_factory() as session:
        await session.execute(
            delete(QuestionSourceProvenance).where(
                QuestionSourceProvenance.profile_id == profile_id
            )
        )
        await session.execute(
            delete(QuestionDiscoveryImport).where(QuestionDiscoveryImport.profile_id == profile_id)
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


@pytest_asyncio.fixture
async def import_fixture() -> ImportFixture:
    fixture = await _make_fixture()
    yield fixture
    await _clear_fixture(fixture.profile_id)
    await engine.dispose()


def _request(
    fixture: ImportFixture,
    *,
    idempotency_key: str,
    items: tuple[DiscoveryImportItem, ...] | None = None,
) -> DiscoveryImportRequest:
    return DiscoveryImportRequest(
        run_id=fixture.run_id,
        bank_id=fixture.bank_id,
        idempotency_key=idempotency_key,
        items=items
        or tuple(
            DiscoveryImportItem(
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
            )
            for candidate in fixture.candidates
        ),
    )


async def _profile_counts(profile_id: uuid.UUID) -> dict[str, int]:
    async with async_session_factory() as session:
        question_count = await session.scalar(
            select(func.count())
            .select_from(Question)
            .join(QuestionBank, QuestionBank.id == Question.bank_id)
            .where(QuestionBank.profile_id == profile_id)
        )
        import_count = await session.scalar(
            select(func.count())
            .select_from(QuestionDiscoveryImport)
            .where(QuestionDiscoveryImport.profile_id == profile_id)
        )
        provenance_count = await session.scalar(
            select(func.count())
            .select_from(QuestionSourceProvenance)
            .where(QuestionSourceProvenance.profile_id == profile_id)
        )
    return {
        "questions": int(question_count or 0),
        "imports": int(import_count or 0),
        "provenance": int(provenance_count or 0),
    }


@pytest.mark.asyncio
async def test_import_creates_draft_link_question_tags_audit_and_immutable_provenance(
    import_fixture: ImportFixture,
) -> None:
    candidate = import_fixture.candidates[0]
    request = _request(
        import_fixture,
        idempotency_key="import-success",
        items=(
            DiscoveryImportItem(
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
                difficulty=Difficulty.EXPERT,
                tag_names=("RAG", "reviewed"),
                user_note="Reviewed before activation.",
            ),
        ),
    )

    async with async_session_factory() as session:
        result = await import_discovery_candidates(session, import_fixture.profile_id, request)

    assert result.replayed is False
    assert len(result.items) == 1
    imported = result.items[0]

    async with async_session_factory() as session:
        question = await session.get(Question, imported.question_id)
        assert question is not None
        tag_names = (
            await session.scalars(
                select(QuestionTag.name)
                .join(QuestionTagLink, QuestionTagLink.tag_id == QuestionTag.id)
                .where(QuestionTagLink.question_id == question.id)
                .order_by(QuestionTag.name)
            )
        ).all()
        import_audit = await session.get(QuestionDiscoveryImport, imported.import_id)
        provenance = await session.scalar(
            select(QuestionSourceProvenance).where(
                QuestionSourceProvenance.question_id == question.id
            )
        )
        candidate_row = await session.get(QuestionDiscoveryCandidate, candidate.id)
        variants = await session.scalar(
            select(func.count())
            .select_from(QuestionVariant)
            .where(QuestionVariant.question_id == question.id)
        )
        source = await session.get(QuestionDiscoverySource, import_fixture.source_id)
        assert source is not None
        source.title = "Changed after import"
        source.attribution = {"source_kind": "changed"}
        await session.commit()

    assert question.status == QuestionStatus.DRAFT
    assert question.source_type == SourceType.LINK_IMPORT
    assert question.difficulty == Difficulty.EXPERT
    assert question.user_note == "Reviewed before activation."
    assert tag_names == ["RAG", "reviewed"]
    assert import_audit is not None
    assert import_audit.status == DiscoveryImportStatus.SUCCEEDED
    assert import_audit.question_id == question.id
    assert provenance is not None
    assert provenance.discovery_run_id == import_fixture.run_id
    assert provenance.candidate_id == candidate.id
    assert provenance.source_title == "Public LLM interview notes"
    assert provenance.normalized_url == "https://public.example.test/llm-interview-notes"
    assert provenance.excerpt == "retrieval evaluation evidence 0"
    assert provenance.attribution == {"source_kind": "community_notes", "official": False}
    assert candidate_row is not None
    assert candidate_row.status == DiscoveryCandidateStatus.IMPORTED
    assert candidate_row.import_count == 1
    assert variants == 0


@pytest.mark.asyncio
async def test_same_key_same_body_replays_original_import_without_new_rows(
    import_fixture: ImportFixture,
) -> None:
    candidate = import_fixture.candidates[0]
    request = _request(
        import_fixture,
        idempotency_key="import-replay",
        items=(
            DiscoveryImportItem(
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
            ),
        ),
    )

    async with async_session_factory() as session:
        first = await import_discovery_candidates(session, import_fixture.profile_id, request)
    async with async_session_factory() as session:
        replay = await import_discovery_candidates(session, import_fixture.profile_id, request)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.batch_id == first.batch_id
    assert replay.items == first.items
    assert await _profile_counts(import_fixture.profile_id) == {
        "questions": 1,
        "imports": 1,
        "provenance": 1,
    }


@pytest.mark.asyncio
async def test_same_key_with_changed_body_returns_conflict_without_new_rows(
    import_fixture: ImportFixture,
) -> None:
    candidate = import_fixture.candidates[0]
    first_request = _request(
        import_fixture,
        idempotency_key="import-conflict",
        items=(
            DiscoveryImportItem(
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
            ),
        ),
    )
    changed_request = _request(
        import_fixture,
        idempotency_key="import-conflict",
        items=(
            DiscoveryImportItem(
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
                prompt="A materially changed import prompt.",
            ),
        ),
    )

    async with async_session_factory() as session:
        await import_discovery_candidates(session, import_fixture.profile_id, first_request)
    async with async_session_factory() as session:
        with pytest.raises(AppError) as error:
            await import_discovery_candidates(session, import_fixture.profile_id, changed_request)

    assert error.value.code == "discovery_import_conflict"
    assert await _profile_counts(import_fixture.profile_id) == {
        "questions": 1,
        "imports": 1,
        "provenance": 1,
    }


@pytest.mark.asyncio
async def test_stale_candidate_in_batch_rolls_back_every_item(
    import_fixture: ImportFixture,
) -> None:
    first, stale = import_fixture.candidates
    request = _request(
        import_fixture,
        idempotency_key="stale-batch",
        items=(
            DiscoveryImportItem(
                candidate_id=first.id,
                candidate_revision=first.revision,
            ),
            DiscoveryImportItem(
                candidate_id=stale.id,
                candidate_revision=stale.revision + 1,
            ),
        ),
    )

    async with async_session_factory() as session:
        with pytest.raises(AppError) as error:
            await import_discovery_candidates(session, import_fixture.profile_id, request)

    assert error.value.code == "discovery_candidate_stale"
    assert await _profile_counts(import_fixture.profile_id) == {
        "questions": 0,
        "imports": 0,
        "provenance": 0,
    }
    async with async_session_factory() as session:
        candidates = (
            await session.scalars(
                select(QuestionDiscoveryCandidate)
                .where(QuestionDiscoveryCandidate.profile_id == import_fixture.profile_id)
                .order_by(QuestionDiscoveryCandidate.created_at)
            )
        ).all()
    assert all(candidate.status == DiscoveryCandidateStatus.PROPOSED for candidate in candidates)
    assert all(candidate.import_count == 0 for candidate in candidates)


@pytest.mark.asyncio
async def test_exact_bank_duplicate_in_batch_rolls_back_other_candidate(
    import_fixture: ImportFixture,
) -> None:
    valid, duplicate = import_fixture.candidates
    async with async_session_factory() as session:
        session.add(
            Question(
                bank_id=import_fixture.bank_id,
                prompt=duplicate.prompt,
                normalized_hash=prompt_hash(duplicate.prompt),
                status=QuestionStatus.ACTIVE,
                source_type=SourceType.MANUAL,
            )
        )
        await session.commit()

    request = _request(
        import_fixture,
        idempotency_key="duplicate-batch",
        items=(
            DiscoveryImportItem(candidate_id=valid.id, candidate_revision=valid.revision),
            DiscoveryImportItem(candidate_id=duplicate.id, candidate_revision=duplicate.revision),
        ),
    )
    async with async_session_factory() as session:
        with pytest.raises(AppError) as error:
            await import_discovery_candidates(session, import_fixture.profile_id, request)

    assert error.value.code == "question_duplicate"
    assert await _profile_counts(import_fixture.profile_id) == {
        "questions": 1,
        "imports": 0,
        "provenance": 0,
    }


@pytest.mark.asyncio
async def test_archived_bank_duplicate_is_rejected_before_insert(
    import_fixture: ImportFixture,
) -> None:
    candidate = import_fixture.candidates[0]
    async with async_session_factory() as session:
        archived = Question(
            bank_id=import_fixture.bank_id,
            prompt=candidate.prompt,
            normalized_hash=prompt_hash(candidate.prompt),
            status=QuestionStatus.ARCHIVED,
            source_type=SourceType.MANUAL,
        )
        archived.soft_delete()
        session.add(archived)
        await session.commit()

    request = _request(
        import_fixture,
        idempotency_key="archived-duplicate",
        items=(
            DiscoveryImportItem(
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
            ),
        ),
    )
    async with async_session_factory() as session:
        with pytest.raises(AppError) as error:
            await import_discovery_candidates(session, import_fixture.profile_id, request)

    assert error.value.code == "question_duplicate"
    assert await _profile_counts(import_fixture.profile_id) == {
        "questions": 1,
        "imports": 0,
        "provenance": 0,
    }


@pytest.mark.asyncio
async def test_provenance_and_import_history_survive_temporary_run_cleanup(
    import_fixture: ImportFixture,
) -> None:
    candidate = import_fixture.candidates[0]
    request = _request(
        import_fixture,
        idempotency_key="cleanup-history",
        items=(
            DiscoveryImportItem(
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
            ),
        ),
    )
    async with async_session_factory() as session:
        result = await import_discovery_candidates(session, import_fixture.profile_id, request)

    async with async_session_factory() as session:
        run = await session.get(QuestionDiscoveryRun, import_fixture.run_id)
        assert run is not None
        await session.delete(run)
        await session.commit()

    async with async_session_factory() as session:
        import_audit = await session.get(QuestionDiscoveryImport, result.items[0].import_id)
        provenance = await session.scalar(
            select(QuestionSourceProvenance).where(
                QuestionSourceProvenance.question_id == result.items[0].question_id
            )
        )
        question = await session.get(Question, result.items[0].question_id)

    assert import_audit is not None
    assert import_audit.candidate_id is None
    assert provenance is not None
    assert provenance.discovery_run_id is None
    assert provenance.candidate_id is None
    assert provenance.normalized_url == "https://public.example.test/llm-interview-notes"
    assert question is not None
    assert question.status == QuestionStatus.DRAFT
