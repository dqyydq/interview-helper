import pytest
import pytest_asyncio
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.db.models import SQLModel
from app.db.models.common import DiscoveryProviderType, DiscoverySourceMode
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


@pytest_asyncio.fixture(autouse=True)
async def dispose_pool_after_test():
    yield
    await engine.dispose()


def _unique_names(connection: Connection, table: str) -> set[str]:
    inspector = inspect(connection)
    constraint_names = {
        item["name"] for item in inspector.get_unique_constraints(table) if item["name"]
    }
    index_names = {
        item["name"] for item in inspector.get_indexes(table) if item["name"] and item.get("unique")
    }
    return constraint_names | index_names


def _foreign_key_actions(connection: Connection, table: str) -> dict[str, str | None]:
    inspector = inspect(connection)
    return {
        constraint["constrained_columns"][0]: constraint["options"].get("ondelete")
        for constraint in inspector.get_foreign_keys(table)
    }


def _foreign_key_actions_by_name(connection: Connection, table: str) -> dict[str, str | None]:
    inspector = inspect(connection)
    return {
        constraint["name"]: constraint["options"].get("ondelete")
        for constraint in inspector.get_foreign_keys(table)
        if constraint["name"]
    }


@pytest.mark.asyncio
async def test_all_metadata_tables_exist_in_postgresql() -> None:
    async with engine.connect() as connection:
        database_tables = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )

    assert set(SQLModel.metadata.tables) <= database_tables
    assert len(SQLModel.metadata.tables) == 43


@pytest.mark.asyncio
async def test_database_contains_critical_unique_constraints() -> None:
    async with engine.connect() as connection:
        message_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(sync_connection, "interview_messages")
        )
        job_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(sync_connection, "background_jobs")
        )
        memory_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(sync_connection, "memory_items")
        )
        round_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(sync_connection, "round_profiles")
        )
        resume_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(sync_connection, "resumes")
        )
        connector_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(sync_connection, "discovery_connectors")
        )
        source_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(sync_connection, "question_discovery_sources")
        )
        candidate_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(
                sync_connection, "question_discovery_candidates"
            )
        )
        evidence_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(
                sync_connection, "question_discovery_candidate_evidence"
            )
        )
        import_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(sync_connection, "question_discovery_imports")
        )
        provenance_constraints = await connection.run_sync(
            lambda sync_connection: _unique_names(sync_connection, "question_source_provenance")
        )

    assert "uq_interview_message_sequence" in message_constraints
    assert "ix_background_jobs_idempotency_key" in job_constraints
    assert "uq_memory_item_version" in memory_constraints
    assert "uq_round_profile_sequence" in round_constraints
    assert "uq_resume_profile_hash" in resume_constraints
    assert "uq_discovery_connector_active_name" in connector_constraints
    assert "uq_discovery_source_run_url" in source_constraints
    assert "uq_discovery_candidate_run_content" in candidate_constraints
    assert "uq_discovery_candidate_evidence" in evidence_constraints
    assert "uq_discovery_import_idempotency_candidate" in import_constraints
    assert "uq_discovery_import_success_candidate_bank" in import_constraints
    assert "uq_question_source_provenance_evidence" in provenance_constraints


@pytest.mark.asyncio
async def test_database_foreign_keys_preserve_history_and_privacy_boundaries() -> None:
    async with engine.connect() as connection:
        plan_question_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions(sync_connection, "plan_questions")
        )
        config_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions(sync_connection, "interview_configs")
        )
        message_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions(sync_connection, "interview_messages")
        )
        memory_source_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions(sync_connection, "memory_sources")
        )
        company_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions(sync_connection, "companies")
        )
        source_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions_by_name(
                sync_connection, "question_discovery_sources"
            )
        )
        candidate_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions_by_name(
                sync_connection, "question_discovery_candidates"
            )
        )
        evidence_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions_by_name(
                sync_connection, "question_discovery_candidate_evidence"
            )
        )
        import_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions_by_name(
                sync_connection, "question_discovery_imports"
            )
        )
        provenance_actions = await connection.run_sync(
            lambda sync_connection: _foreign_key_actions_by_name(
                sync_connection, "question_source_provenance"
            )
        )

    assert plan_question_actions["question_id"] == "RESTRICT"
    assert config_actions["resume_id"] == "SET NULL"
    assert message_actions["session_id"] == "CASCADE"
    assert memory_source_actions["session_id"] == "CASCADE"
    assert company_actions["profile_id"] == "CASCADE"
    assert source_actions["fk_discovery_source_run_profile"] == "CASCADE"
    assert candidate_actions["fk_discovery_candidate_run_profile"] == "CASCADE"
    assert evidence_actions["fk_discovery_evidence_candidate_run_profile"] == "CASCADE"
    assert evidence_actions["fk_discovery_evidence_source_run_profile"] == "CASCADE"
    assert import_actions["fk_discovery_import_candidate_profile"] == "SET NULL (candidate_id)"
    assert provenance_actions["fk_provenance_run_profile"] == "SET NULL (discovery_run_id)"
    assert provenance_actions["fk_provenance_candidate_profile"] == "SET NULL (candidate_id)"
    assert provenance_actions["fk_provenance_candidate_run_profile"] == "SET NULL (candidate_id)"


@pytest.mark.asyncio
async def test_discovery_evidence_cannot_cross_runs() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Discovery constraint test")
        session.add(profile)
        await session.flush()

        connector = DiscoveryConnector(
            profile_id=profile.id,
            name="Tavily constraint test",
            provider_type=DiscoveryProviderType.TAVILY,
        )
        session.add(connector)
        await session.flush()

        first_run = QuestionDiscoveryRun(
            profile_id=profile.id,
            connector_id=connector.id,
            connector_configuration_version=connector.configuration_version,
            source_mode=DiscoverySourceMode.SEARCH,
        )
        second_run = QuestionDiscoveryRun(
            profile_id=profile.id,
            connector_id=connector.id,
            connector_configuration_version=connector.configuration_version,
            source_mode=DiscoverySourceMode.URLS,
        )
        session.add_all([first_run, second_run])
        await session.flush()

        candidate = QuestionDiscoveryCandidate(
            profile_id=profile.id,
            run_id=first_run.id,
            prompt="如何设计一个来源可追溯的题库导入流程？",
            content_hash="a" * 64,
        )
        source = QuestionDiscoverySource(
            profile_id=profile.id,
            run_id=second_run.id,
            normalized_url="https://example.com/interview",
            domain="example.com",
        )
        session.add_all([candidate, source])
        await session.flush()

        session.add(
            QuestionDiscoveryCandidateEvidence(
                profile_id=profile.id,
                run_id=first_run.id,
                candidate_id=candidate.id,
                source_id=source.id,
                excerpt="来源位于另一次发现运行，必须被数据库约束拒绝。",
                evidence_hash="b" * 64,
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()

        await session.rollback()


@pytest.mark.asyncio
async def test_discovery_connector_active_names_are_case_insensitive() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Case-insensitive connector test")
        session.add(profile)
        await session.flush()

        session.add(
            DiscoveryConnector(
                profile_id=profile.id,
                name="Tavily Search",
                provider_type=DiscoveryProviderType.TAVILY,
            )
        )
        await session.flush()

        session.add(
            DiscoveryConnector(
                profile_id=profile.id,
                name="tavily search",
                provider_type=DiscoveryProviderType.TAVILY,
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()

        await session.rollback()


@pytest.mark.asyncio
async def test_soft_deleted_discovery_connector_name_can_be_reused() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Connector name reuse test")
        session.add(profile)
        await session.flush()

        connector = DiscoveryConnector(
            profile_id=profile.id,
            name="Tavily Search",
            provider_type=DiscoveryProviderType.TAVILY,
        )
        session.add(connector)
        await session.flush()

        connector.soft_delete()
        await session.flush()

        replacement = DiscoveryConnector(
            profile_id=profile.id,
            name="tavily search",
            provider_type=DiscoveryProviderType.TAVILY,
        )
        session.add(replacement)
        await session.flush()

        assert replacement.id != connector.id

        await session.rollback()


@pytest.mark.asyncio
async def test_discovery_evidence_cannot_cross_profiles() -> None:
    async with async_session_factory() as session:
        first_profile = UserProfile(display_name="First discovery profile")
        second_profile = UserProfile(display_name="Second discovery profile")
        session.add_all([first_profile, second_profile])
        await session.flush()

        first_connector = DiscoveryConnector(
            profile_id=first_profile.id,
            name="First Tavily connector",
            provider_type=DiscoveryProviderType.TAVILY,
        )
        second_connector = DiscoveryConnector(
            profile_id=second_profile.id,
            name="Second Tavily connector",
            provider_type=DiscoveryProviderType.TAVILY,
        )
        session.add_all([first_connector, second_connector])
        await session.flush()

        first_run = QuestionDiscoveryRun(
            profile_id=first_profile.id,
            connector_id=first_connector.id,
            connector_configuration_version=first_connector.configuration_version,
            source_mode=DiscoverySourceMode.SEARCH,
        )
        second_run = QuestionDiscoveryRun(
            profile_id=second_profile.id,
            connector_id=second_connector.id,
            connector_configuration_version=second_connector.configuration_version,
            source_mode=DiscoverySourceMode.SEARCH,
        )
        session.add_all([first_run, second_run])
        await session.flush()

        candidate = QuestionDiscoveryCandidate(
            profile_id=first_profile.id,
            run_id=first_run.id,
            prompt="如何验证跨 profile 的来源关联会被拒绝？",
            content_hash="c" * 64,
        )
        source = QuestionDiscoverySource(
            profile_id=second_profile.id,
            run_id=second_run.id,
            normalized_url="https://example.org/interview",
            domain="example.org",
        )
        session.add_all([candidate, source])
        await session.flush()

        session.add(
            QuestionDiscoveryCandidateEvidence(
                profile_id=first_profile.id,
                run_id=first_run.id,
                candidate_id=candidate.id,
                source_id=source.id,
                excerpt="不同 profile 的来源不能作为当前候选的证据。",
                evidence_hash="d" * 64,
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()

        await session.rollback()


@pytest.mark.asyncio
async def test_discovery_cleanup_keeps_import_and_provenance_history() -> None:
    async with async_session_factory() as session:
        profile = UserProfile(display_name="Discovery cleanup test")
        session.add(profile)
        await session.flush()

        connector = DiscoveryConnector(
            profile_id=profile.id,
            name="Cleanup Tavily connector",
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
        bank = QuestionBank(profile_id=profile.id, name="Cleanup question bank")
        session.add_all([run, bank])
        await session.flush()

        candidate = QuestionDiscoveryCandidate(
            profile_id=profile.id,
            run_id=run.id,
            prompt="导入后清理发现过程是否会保留来源追溯？",
            content_hash="e" * 64,
        )
        session.add(candidate)
        await session.flush()

        question = Question(
            bank_id=bank.id,
            prompt=candidate.prompt,
            normalized_hash="f" * 64,
        )
        session.add(question)
        await session.flush()

        imported = QuestionDiscoveryImport(
            profile_id=profile.id,
            candidate_id=candidate.id,
            candidate_content_hash=candidate.content_hash,
            bank_id=bank.id,
            question_id=question.id,
            idempotency_key="cleanup-import-key",
            request_hash="g" * 64,
            candidate_revision=candidate.candidate_revision,
        )
        provenance = QuestionSourceProvenance(
            profile_id=profile.id,
            question_id=question.id,
            discovery_run_id=run.id,
            candidate_id=candidate.id,
            source_title="Cleanup source",
            normalized_url="https://example.net/cleanup",
            source_domain="example.net",
            source_category="technical_community",
            excerpt="清理临时发现数据后，这段最小归因仍应保留。",
            evidence_hash="h" * 64,
        )
        session.add_all([imported, provenance])
        await session.flush()

        await session.delete(run)
        await session.flush()
        await session.refresh(imported)
        await session.refresh(provenance)

        assert imported.candidate_id is None
        assert provenance.discovery_run_id is None
        assert provenance.candidate_id is None

        await session.rollback()
