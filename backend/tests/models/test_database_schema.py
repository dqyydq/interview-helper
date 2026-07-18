import pytest
import pytest_asyncio
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.db.models import SQLModel
from app.db.session import engine


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
        item["name"]
        for item in inspector.get_indexes(table)
        if item["name"] and item.get("unique")
    }
    return constraint_names | index_names


def _foreign_key_actions(connection: Connection, table: str) -> dict[str, str | None]:
    inspector = inspect(connection)
    return {
        constraint["constrained_columns"][0]: constraint["options"].get("ondelete")
        for constraint in inspector.get_foreign_keys(table)
    }


@pytest.mark.asyncio
async def test_all_metadata_tables_exist_in_postgresql() -> None:
    async with engine.connect() as connection:
        database_tables = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )

    assert set(SQLModel.metadata.tables) <= database_tables
    assert len(SQLModel.metadata.tables) == 35


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

    assert "uq_interview_message_sequence" in message_constraints
    assert "ix_background_jobs_idempotency_key" in job_constraints
    assert "uq_memory_item_version" in memory_constraints
    assert "uq_round_profile_sequence" in round_constraints
    assert "uq_resume_profile_hash" in resume_constraints


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

    assert plan_question_actions["question_id"] == "RESTRICT"
    assert config_actions["resume_id"] == "SET NULL"
    assert message_actions["session_id"] == "CASCADE"
    assert memory_source_actions["session_id"] == "CASCADE"
    assert company_actions["profile_id"] == "CASCADE"
