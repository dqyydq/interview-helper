"""Add source-aware question discovery records.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _entity_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "discovery_connectors",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("configuration", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_summary", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "id",
            name="uq_discovery_connector_profile_identity",
        ),
    )
    op.create_index(
        op.f("ix_discovery_connectors_profile_id"),
        "discovery_connectors",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_discovery_connectors_provider_type"),
        "discovery_connectors",
        ["provider_type"],
    )
    op.create_index(
        op.f("ix_discovery_connectors_enabled"),
        "discovery_connectors",
        ["enabled"],
    )
    op.create_index(
        op.f("ix_discovery_connectors_status"),
        "discovery_connectors",
        ["status"],
    )
    op.create_index(
        "uq_discovery_connector_active_name",
        "discovery_connectors",
        ["profile_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "question_discovery_runs",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("connector_configuration_version", sa.Integer(), nullable=False),
        sa.Column("initiated_by", sa.String(length=120), nullable=False),
        sa.Column("source_mode", sa.String(length=32), nullable=False),
        sa.Column("query_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("failed_source_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_id", "connector_id"],
            ["discovery_connectors.profile_id", "discovery_connectors.id"],
            name="fk_discovery_run_connector_profile",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "id",
            name="uq_discovery_run_profile_identity",
        ),
    )
    op.create_index(
        op.f("ix_question_discovery_runs_profile_id"),
        "question_discovery_runs",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_runs_connector_id"),
        "question_discovery_runs",
        ["connector_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_runs_source_mode"),
        "question_discovery_runs",
        ["source_mode"],
    )
    op.create_index(
        op.f("ix_question_discovery_runs_status"),
        "question_discovery_runs",
        ["status"],
    )
    op.create_index(
        op.f("ix_question_discovery_runs_stage"),
        "question_discovery_runs",
        ["stage"],
    )
    op.create_index(
        op.f("ix_question_discovery_runs_expires_at"),
        "question_discovery_runs",
        ["expires_at"],
    )
    op.create_index(
        "ix_discovery_runs_profile_status_expires_at",
        "question_discovery_runs",
        ["profile_id", "status", "expires_at"],
    )

    op.create_table(
        "question_discovery_sources",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("raw_url", sa.Text(), nullable=True),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("source_category", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("attribution", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_id", "run_id"],
            ["question_discovery_runs.profile_id", "question_discovery_runs.id"],
            name="fk_discovery_source_run_profile",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "run_id",
            "id",
            name="uq_discovery_source_profile_run_identity",
        ),
        sa.UniqueConstraint("run_id", "normalized_url", name="uq_discovery_source_run_url"),
    )
    op.create_index(
        op.f("ix_question_discovery_sources_profile_id"),
        "question_discovery_sources",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_sources_run_id"),
        "question_discovery_sources",
        ["run_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_sources_domain"),
        "question_discovery_sources",
        ["domain"],
    )
    op.create_index(
        op.f("ix_question_discovery_sources_source_category"),
        "question_discovery_sources",
        ["source_category"],
    )
    op.create_index(
        op.f("ix_question_discovery_sources_status"),
        "question_discovery_sources",
        ["status"],
    )
    op.create_index(
        op.f("ix_question_discovery_sources_content_hash"),
        "question_discovery_sources",
        ["content_hash"],
    )
    op.create_index(
        op.f("ix_question_discovery_sources_expires_at"),
        "question_discovery_sources",
        ["expires_at"],
    )
    op.create_index(
        "ix_discovery_sources_profile_status_expires_at",
        "question_discovery_sources",
        ["profile_id", "status", "expires_at"],
    )

    op.create_table(
        "question_discovery_candidates",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("question_type", sa.String(length=32), nullable=False),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("suggested_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("suggested_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("suggested_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("applicable_companies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("applicable_rounds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reference_points", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("follow_up_suggestions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("matching_reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("researcher_connection_id", sa.Uuid(), nullable=True),
        sa.Column("researcher_model_name", sa.String(length=255), nullable=True),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("similar_question_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("import_count", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_id", "run_id"],
            ["question_discovery_runs.profile_id", "question_discovery_runs.id"],
            name="fk_discovery_candidate_run_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["researcher_connection_id"],
            ["model_connections.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "id",
            name="uq_discovery_candidate_profile_identity",
        ),
        sa.UniqueConstraint(
            "profile_id",
            "run_id",
            "id",
            name="uq_discovery_candidate_profile_run_identity",
        ),
        sa.UniqueConstraint(
            "profile_id",
            "run_id",
            "content_hash",
            name="uq_discovery_candidate_run_content",
        ),
    )
    op.create_index(
        op.f("ix_question_discovery_candidates_profile_id"),
        "question_discovery_candidates",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidates_run_id"),
        "question_discovery_candidates",
        ["run_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidates_question_type"),
        "question_discovery_candidates",
        ["question_type"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidates_difficulty"),
        "question_discovery_candidates",
        ["difficulty"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidates_researcher_connection_id"),
        "question_discovery_candidates",
        ["researcher_connection_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidates_content_hash"),
        "question_discovery_candidates",
        ["content_hash"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidates_status"),
        "question_discovery_candidates",
        ["status"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidates_expires_at"),
        "question_discovery_candidates",
        ["expires_at"],
    )
    op.create_index(
        "ix_discovery_candidates_profile_status_expires_at",
        "question_discovery_candidates",
        ["profile_id", "status", "expires_at"],
    )

    op.create_table(
        "question_discovery_candidate_evidence",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("source_locator", sa.String(length=500), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_id", "run_id"],
            ["question_discovery_runs.profile_id", "question_discovery_runs.id"],
            name="fk_discovery_evidence_run_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "run_id", "candidate_id"],
            [
                "question_discovery_candidates.profile_id",
                "question_discovery_candidates.run_id",
                "question_discovery_candidates.id",
            ],
            name="fk_discovery_evidence_candidate_run_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "run_id", "source_id"],
            [
                "question_discovery_sources.profile_id",
                "question_discovery_sources.run_id",
                "question_discovery_sources.id",
            ],
            name="fk_discovery_evidence_source_run_profile",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "candidate_id",
            "source_id",
            "evidence_hash",
            name="uq_discovery_candidate_evidence",
        ),
    )
    op.create_index(
        op.f("ix_question_discovery_candidate_evidence_profile_id"),
        "question_discovery_candidate_evidence",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidate_evidence_run_id"),
        "question_discovery_candidate_evidence",
        ["run_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidate_evidence_candidate_id"),
        "question_discovery_candidate_evidence",
        ["candidate_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidate_evidence_source_id"),
        "question_discovery_candidate_evidence",
        ["source_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_candidate_evidence_expires_at"),
        "question_discovery_candidate_evidence",
        ["expires_at"],
    )
    op.create_index(
        "ix_discovery_evidence_profile_expires_at",
        "question_discovery_candidate_evidence",
        ["profile_id", "expires_at"],
    )

    op.create_table(
        "question_discovery_imports",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_content_hash", sa.String(length=128), nullable=False),
        sa.Column("bank_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bank_id"], ["question_banks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["profile_id", "candidate_id"],
            ["question_discovery_candidates.profile_id", "question_discovery_candidates.id"],
            name="fk_discovery_import_candidate_profile",
            ondelete="SET NULL (candidate_id)",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "idempotency_key",
            "candidate_content_hash",
            name="uq_discovery_import_idempotency_candidate",
        ),
    )
    op.create_index(
        op.f("ix_question_discovery_imports_profile_id"),
        "question_discovery_imports",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_imports_candidate_id"),
        "question_discovery_imports",
        ["candidate_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_imports_candidate_content_hash"),
        "question_discovery_imports",
        ["candidate_content_hash"],
    )
    op.create_index(
        op.f("ix_question_discovery_imports_bank_id"),
        "question_discovery_imports",
        ["bank_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_imports_question_id"),
        "question_discovery_imports",
        ["question_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_imports_idempotency_key"),
        "question_discovery_imports",
        ["idempotency_key"],
    )
    op.create_index(
        op.f("ix_question_discovery_imports_request_hash"),
        "question_discovery_imports",
        ["request_hash"],
    )
    op.create_index(
        op.f("ix_question_discovery_imports_batch_id"),
        "question_discovery_imports",
        ["batch_id"],
    )
    op.create_index(
        op.f("ix_question_discovery_imports_status"),
        "question_discovery_imports",
        ["status"],
    )
    op.create_index(
        "ix_discovery_imports_profile_status",
        "question_discovery_imports",
        ["profile_id", "status"],
    )
    op.create_index(
        "uq_discovery_import_success_candidate_bank",
        "question_discovery_imports",
        ["profile_id", "candidate_id", "bank_id"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )

    op.create_table(
        "question_source_provenance",
        *_entity_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("discovery_run_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("source_title", sa.String(length=500), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("source_domain", sa.String(length=255), nullable=False),
        sa.Column("source_category", sa.String(length=48), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=128), nullable=False),
        sa.Column("attribution", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_id", "discovery_run_id"],
            ["question_discovery_runs.profile_id", "question_discovery_runs.id"],
            name="fk_provenance_run_profile",
            ondelete="SET NULL (discovery_run_id)",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "candidate_id"],
            ["question_discovery_candidates.profile_id", "question_discovery_candidates.id"],
            name="fk_provenance_candidate_profile",
            ondelete="SET NULL (candidate_id)",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "discovery_run_id", "candidate_id"],
            [
                "question_discovery_candidates.profile_id",
                "question_discovery_candidates.run_id",
                "question_discovery_candidates.id",
            ],
            name="fk_provenance_candidate_run_profile",
            ondelete="SET NULL (candidate_id)",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "question_id",
            "normalized_url",
            "evidence_hash",
            name="uq_question_source_provenance_evidence",
        ),
    )
    op.create_index(
        op.f("ix_question_source_provenance_profile_id"),
        "question_source_provenance",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_question_source_provenance_question_id"),
        "question_source_provenance",
        ["question_id"],
    )
    op.create_index(
        op.f("ix_question_source_provenance_discovery_run_id"),
        "question_source_provenance",
        ["discovery_run_id"],
    )
    op.create_index(
        op.f("ix_question_source_provenance_candidate_id"),
        "question_source_provenance",
        ["candidate_id"],
    )
    op.create_index(
        op.f("ix_question_source_provenance_source_domain"),
        "question_source_provenance",
        ["source_domain"],
    )
    op.create_index(
        op.f("ix_question_source_provenance_source_category"),
        "question_source_provenance",
        ["source_category"],
    )
    op.create_index(
        "ix_question_source_provenance_profile_question",
        "question_source_provenance",
        ["profile_id", "question_id"],
    )


def downgrade() -> None:
    op.drop_table("question_source_provenance")
    op.drop_index(
        "uq_discovery_import_success_candidate_bank", table_name="question_discovery_imports"
    )
    op.drop_index("ix_discovery_imports_profile_status", table_name="question_discovery_imports")
    op.drop_index(
        op.f("ix_question_discovery_imports_status"), table_name="question_discovery_imports"
    )
    op.drop_index(
        op.f("ix_question_discovery_imports_batch_id"), table_name="question_discovery_imports"
    )
    op.drop_index(
        op.f("ix_question_discovery_imports_request_hash"), table_name="question_discovery_imports"
    )
    op.drop_index(
        op.f("ix_question_discovery_imports_idempotency_key"),
        table_name="question_discovery_imports",
    )
    op.drop_index(
        op.f("ix_question_discovery_imports_question_id"), table_name="question_discovery_imports"
    )
    op.drop_index(
        op.f("ix_question_discovery_imports_bank_id"), table_name="question_discovery_imports"
    )
    op.drop_index(
        op.f("ix_question_discovery_imports_candidate_content_hash"),
        table_name="question_discovery_imports",
    )
    op.drop_index(
        op.f("ix_question_discovery_imports_candidate_id"), table_name="question_discovery_imports"
    )
    op.drop_index(
        op.f("ix_question_discovery_imports_profile_id"), table_name="question_discovery_imports"
    )
    op.drop_table("question_discovery_imports")
    op.drop_table("question_discovery_candidate_evidence")
    op.drop_table("question_discovery_candidates")
    op.drop_table("question_discovery_sources")
    op.drop_table("question_discovery_runs")
    op.drop_table("discovery_connectors")
